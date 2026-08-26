"""Controller view — one install's management surface (roadmap 4.3).

Tabs: **Server** (start/stop/status with the README §12 port-conflict message),
**Console** (live worldserver log, a console command line, an accounts form),
**Modules** (the manifests the store knows, install/remove through the shared
applier, the rebuild/restart the report asks for), **Networking** (LAN /
internet play via `networking.plan()` + `apply()`, showing the router steps the
app cannot do). The view only calls down into `Controller`, `Applier`,
`console`, `networking` and signals up; it never shells out itself
(style-guide §3/§5). Every external call is a seam in `ControllerServices` so
the view is testable offscreen with fakes.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from yulon import docker, networking
from yulon.apply import Applier, ApplyReport, DockerSql
from yulon.catalog.catalog import CatalogEntry
from yulon.controller import Controller, InstallStatus, PortConflictError
from yulon.controller_wow_wotlk import accounts as wotlk_accounts
from yulon.controller_wow_wotlk import console as wotlk_console
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.controller_wow_wotlk import repair as wotlk_repair
from yulon.log import get_logger
from yulon.manifest import Manifest
from yulon.manifest_store import FAMILY_FILES, ManifestStore
from yulon.networking import Mode, NetworkPlan, NetworkReport
from yulon.ui.widgets.job import JobRunner, LineRelay, threaded_job_runner
from yulon.ui.widgets.log_panel import LogPanel

logger = get_logger(__name__)


@dataclass
class ControllerServices:
    """Everything the view calls down into. Real implementations by default; fakes in tests."""

    controller: Controller
    logs_source: Callable[[], Iterator[str]]
    send_console: Callable[[str], wotlk_console.ConsoleReply]
    store: ManifestStore | None
    applier: Applier | None
    network_plan: Callable[[Mode], NetworkPlan]
    network_apply: Callable[[NetworkPlan], NetworkReport]
    create_account: Callable[[str, str, int], wotlk_accounts.AccountResult]
    backup: Callable[[], wotlk_maintenance.BackupReport]
    backups_dir: Callable[[], Path]
    plan_restore: Callable[[Path], wotlk_maintenance.RestorePlan]
    restore: Callable[[wotlk_maintenance.RestorePlan], wotlk_maintenance.RestoreReport]
    interrupted_restore: Callable[[], wotlk_maintenance.InterruptedRestore | None]
    forget_interrupted: Callable[[], bool]

    @classmethod
    def for_wotlk(
        cls, entry: CatalogEntry, server_dir: Path, client_dir: Path | None = None
    ) -> ControllerServices:
        """The real WotLK wiring for an install at `server_dir`."""
        spec = entry.container_spec()
        # The entry may carry the password, or name a file the installer generated
        # it into; `db_password()` knows both. TBC and Vanilla generate one, so
        # before this they authenticated as root with the literal "password" -
        # Start and Stop need no database, which is why it surfaced later, on
        # Create account and Backup. The default stays as a last resort so an
        # install whose password file has gone missing still gets a tab that can
        # start and stop, rather than no tab at all.
        password = entry.install.db_password(server_dir)
        if password is None:
            # `db_password()` says None when the entry NAMES a password file and
            # that file cannot be read - which is not the same as "use the
            # default", and silently defaulting here would rebuild the bug this
            # seam exists to close. The tab is still built, because Start and
            # Stop need no database and no tab at all is worse; but the reason
            # every SQL-backed control is about to fail is written down once,
            # here, instead of arriving as "access denied" six clicks later.
            if entry.install.db_root_password_file:
                logger.warning(
                    f"{entry.id}: cannot read {entry.install.db_root_password_file} in "
                    f"{server_dir}, so the database password is unknown - accounts, backup "
                    f"and restore will fail until that file is restored"
                )
            password = wotlk_modules.DEFAULT_DB_ROOT_PASSWORD
        # `schemas=` is what keeps a CMaNGOS install off AzerothCore's `acore_*`
        # names. This factory is the only place that holds both the entry and the
        # seam, so it is the only place that can say which schemas exist here.
        sql = DockerSql(spec.db, password, schemas=entry.schema_map())
        # Bound to this entry's own db container, not `mysql_for()`'s
        # `docker_ctl.SPEC.db`, so a catalog entry that names a different one
        # cannot end up backing up somebody else's database.
        mysql = wotlk_maintenance.DockerMysql(spec.db, password)
        # Both seams, because neither answers the whole question on its own:
        # `DockerMysql` can ask what schemas exist without naming one to connect
        # to, `DockerSql` can then read inside them. See `repair.import_state()`.
        controller = Controller(
            spec,
            server_dir,
            # Only for a game that named a one-shot import service. `for_wotlk()`
            # is called for EVERY install in state.json, not just wow-wotlk, and
            # `repair.import_state()` looks for the `acore_*` schemas by name — so
            # attaching it unconditionally told a healthy CMaNGOS install its
            # databases were never imported, and offered it the destructive
            # Repair button. `import_service` is the same fact `repair_import()`
            # already refuses on, so the two agree by construction rather than by
            # a second list of which games are AzerothCore (review, 2026-08-23).
            import_probe=(
                (lambda: wotlk_repair.import_state(sql, mysql)) if spec.import_service else None
            ),
            reset_unfinished=(
                (lambda: wotlk_repair.reset_unfinished(sql, mysql)) if spec.import_service else None
            ),
        )
        return cls(
            controller=controller,
            logs_source=lambda: docker.follow_logs(spec.world),
            send_console=lambda cmd: wotlk_console.send_command(
                cmd,
                container=spec.world,
                prompt=entry.console.prompt,
                prompt_precedes_answer=entry.console.prompt_precedes_answer,
            ),
            store=wotlk_modules.store() if entry.has_manifests else None,
            applier=(
                wotlk_modules.applier(server_dir, sql=sql, client_dir=client_dir)
                if entry.has_manifests
                else None
            ),
            network_plan=lambda mode: networking.plan(entry, mode, bindings=_safe_bindings()),
            network_apply=lambda plan: networking.apply(plan, sql=sql),
            # `gm_level` is passed through rather than defaulted here: the guide
            # pairs every `account create` with `account set gmlevel ... 3`, and
            # copying that would hand administrator to every account made from
            # the tile. The spin box defaults to 0 and the user raises it.
            create_account=lambda name, pw, gm: wotlk_accounts.create_account(
                sql, name, pw, gm_level=gm
            ),
            backup=lambda: wotlk_maintenance.backup(
                server_dir, mysql, spec=spec, core_databases=entry.core_databases()
            ),
            backups_dir=lambda: wotlk_maintenance.backups_dir(server_dir),
            plan_restore=lambda path: wotlk_maintenance.plan_restore(path, server_dir, spec=spec),
            # `confirm=plan.token` is not a rubber stamp: the token can only come
            # from a plan, a plan can only come from a real file, and the human
            # confirmation is the dialog the view puts in front of this call.
            # What the token buys is that no confirmation can be spelled `True`.
            restore=lambda plan: wotlk_maintenance.restore(
                plan, mysql, confirm=plan.token, spec=spec
            ),
            interrupted_restore=lambda: wotlk_maintenance.interrupted_restore(server_dir),
            forget_interrupted=lambda: wotlk_maintenance.forget_interrupted_restore(server_dir),
        )


def _safe_bindings() -> dict[int, str] | None:
    try:
        return docker.published_bindings()
    except docker.DockerCommandError:
        return None


REMOVE_IDLE = "Stop and remove containers…"
REMOVE_ARMED = "Press again to remove"
"""Two labels for one button, because a teardown should not be one click away.

The wording changes rather than a dialog appearing: the explanation is a
paragraph naming what is kept, `problem_label` already renders those, and a
modal would arrive from a worker thread.
"""

REPAIR_IDLE = "Repair: finish the database import…"
REPAIR_ARMED = "Press again to overwrite the databases"
"""The same two-press gesture, for the action that really can destroy data.

Deliberately not a second kind of confirmation. There is one arm/disarm shape
on this tab and both destructive buttons use it, so a user who has learned that
pressing once only arms is not surprised by the one where it would matter most.
The armed wording is where they differ: the teardown's says what is *kept*,
this one says what is *overwritten*.
"""

IMPORT_RUNNING = (
    "Running the database import. A full one takes 10-30 minutes and cannot be stopped once "
    "it has started. What the import is printing:"
)
"""The heading above the import's live output, and the one honest thing to say.

It used to be "Running the database import… this takes several minutes." and
then nothing changed on screen until it finished, which for the action whose
armed copy warns it overwrites databases is indistinguishable from a hang — the
user's only recourse being to kill the app, during a database import.

It says "cannot be stopped" because it cannot, and the tab must not suggest
otherwise: every button on it is disabled while this runs, and there is no
cancel to offer. Abandoning a `compose up` means terminating it, which stops
`ac-db-import` part-way through writing schemas.
"""

_IMPORT_TAIL_LINES = 2
_IMPORT_LINE_CHARS = 110
"""How much of the import's output the label carries: the last two lines, trimmed.

Two, because one line looks static whenever a step is slow while two show which
way it is moving. Trimmed, because this label sits above the rest of the tab and
a single 500-character line of SQL would wrap into five rows and move
everything under it.
"""


class ControllerView(QWidget):
    """Per-install tabs; see module docstring."""

    status_changed = Signal(object)  # InstallStatus
    action_failed = Signal(str)  # user-readable message

    def __init__(
        self,
        entry: CatalogEntry,
        services: ControllerServices,
        *,
        status_poll_ms: int = 5000,
        job_runner: JobRunner | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.services = services
        # Every service call goes through this: on a worker thread in the app,
        # inline in tests (review finding, 2026-08-21 — the window used to
        # freeze for the length of a `docker compose up`).
        self._jobs: JobRunner = job_runner or threaded_job_runner(self)
        self._busy = False
        self._status_pending = False
        self._module_pending: str | None = None
        self._console_pending = False
        self._tabs = QTabWidget(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)

        self._restore_plan: wotlk_maintenance.RestorePlan | None = None
        self._remove_armed = False
        self._import_running = False
        self._repair_armed = False
        # The last answer the database gave about its own import, and whether it
        # has been asked since the database came up. Remembered because the
        # question can only be put while the database is running, and the state
        # this action exists for is one the user reaches by pressing Stop.
        self._import_state: docker.ImportState | None = None
        self._import_asked = False
        # The import talks from a worker thread; this is how what it says gets
        # onto the GUI thread. See `LineRelay` — handing `_import_line` itself
        # down as the sink would call it on the worker thread instead.
        self._import_relay = LineRelay(self)
        self._import_relay.line.connect(self._import_line)
        self._import_tail: deque[str] = deque(maxlen=_IMPORT_TAIL_LINES)
        self._build_server_tab()
        self._build_console_tab()
        self._build_accounts_tab()
        self._build_maintenance_tab()
        self._build_modules_tab()
        self._build_networking_tab()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_status)
        if status_poll_ms > 0:
            self._timer.start(status_poll_ms)

    # ------------------------------------------------------------ server tab

    def _build_server_tab(self) -> None:
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        self.status_label = QLabel("status: unknown", tab)
        # Why a whole label and not a dialog: the stop path's refusals are
        # paragraphs naming containers, projects and the file to edit, and they
        # arrive from a worker thread. It was called `conflict_label` while only
        # `_start_failed` wrote to it; a stop that refused wrote nowhere at all,
        # so a refusal was indistinguishable from the silent bug the refusal
        # exists to prevent (review, 2026-08-22).
        self.problem_label = QLabel("", tab)
        self.problem_label.setWordWrap(True)
        self.problem_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse  # so the remedy can be copied
        )
        self.start_button = QPushButton("Start", tab)
        self.stop_button = QPushButton("Stop", tab)
        self.refresh_button = QPushButton("Refresh", tab)
        # Deliberate, per checklist 6.5: nothing removes a container today, and
        # whatever does must not be a stray click next to Stop. It arms on the
        # first press and acts on the second, and anything else disarms it.
        self.remove_button = QPushButton(REMOVE_IDLE, tab)
        # Hidden unless the database has said there is an unfinished import to
        # finish. A destructive action that is always on screen is one that gets
        # pressed by accident, and this one is only ever right for a broken
        # install — the installer imports on every healthy path.
        self.repair_button = QPushButton(REPAIR_IDLE, tab)
        self.repair_button.setVisible(False)
        self.repair_label = QLabel("", tab)
        self.repair_label.setWordWrap(True)
        self.repair_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.repair_label.setVisible(False)
        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.refresh_button.clicked.connect(self.recheck)
        self.remove_button.clicked.connect(self.remove_containers)
        self.repair_button.clicked.connect(self.repair_import)
        row = QHBoxLayout()
        for b in (
            self.start_button,
            self.stop_button,
            self.refresh_button,
            self.remove_button,
            self.repair_button,
        ):
            row.addWidget(b)
        box.addWidget(QLabel(f"<b>{self.entry.name}</b> — {self.services.controller.server_dir}"))
        box.addWidget(self.status_label)
        box.addLayout(row)
        box.addWidget(self.problem_label)
        box.addWidget(self.repair_label)
        box.addStretch(1)
        self._tabs.addTab(tab, "Server")

    def busy_reason(self) -> str | None:
        """Why this tab must not be torn down yet, or None.

        Only the import. Everything else here finishes inside `shutdown()`'s
        join; a database import runs for 10-30 minutes, which is long enough
        that a user WILL close the window during one — and closing during one
        froze the window for `STOP_GRACE_SECONDS + 30` seconds and then aborted
        the process, because `_JobWorker.run()` calls its work synchronously so
        `thread.quit()` cannot preempt a blocking `subprocess.run`, and a
        QThread destroyed while running aborts rather than warns (0xC0000409,
        verified, and recorded in `main.py`). Refusing the close is the honest
        outcome: the import cannot be stopped, so the only choice available was
        ever between waiting and a crash (review, 2026-08-23).
        """
        if not self._import_running:
            return None
        return (
            "The database import is still running. It cannot be stopped, and closing now would "
            "leave the databases half-written. This window will close normally once the import "
            "finishes — it takes 10-30 minutes, and the Server tab shows what it is doing."
        )

    def shutdown(self) -> None:
        """Stop this tab's timers and join its background jobs (called before teardown)."""
        self._timer.stop()
        self.console_log.stop()
        self.console_log.wait(5000)
        waiter = getattr(self._jobs, "wait", None)
        if callable(waiter):
            # Derived from the grace, not a flat ten seconds. `_JobWorker.run()`
            # calls its work synchronously, so `thread.quit()` cannot interrupt a
            # blocking `subprocess.run` — and `main.py` records that a QThread
            # destroyed while running ABORTS the process (0xC0000409) rather than
            # warning. A stop now takes 58-91s measured, so a ten-second join
            # made that abort the ordinary outcome of closing the window during
            # one. Waiting out the grace is the lesser evil: the alternative is
            # not a faster exit, it is a crash (review, 2026-08-23).
            waiter(int((docker.STOP_GRACE_SECONDS + 30) * 1000))

    # -------------------------------------------------------- background work

    def _run(
        self,
        work: Callable[[], object],
        on_done: Callable[[object], None],
        on_error: Callable[[object], None],
    ) -> None:
        """Run `work` off the GUI thread. `on_done`/`on_error` MUST be this view's own
        bound slots - a plain callable would be delivered on the worker thread."""
        self._jobs(work, on_done, on_error)

    @Slot()
    def refresh_status(self) -> None:
        """Re-read `docker ps` off the GUI thread and update the Server tab.

        Deliberately leaves `problem_label` alone: the five-second poll runs
        immediately after a failed action, and clearing here would wipe the
        explanation before it could be read. The Refresh BUTTON clears it —
        see `recheck()`.
        """
        if self._status_pending:
            return  # a poll is already in flight; never queue them up
        self._status_pending = True
        self._run(self.services.controller.status, self._status_ready, self._status_failed)

    @Slot()
    def recheck(self) -> None:
        """What the Refresh button does: drop the last problem, then re-read status.

        Without this the paragraph outlived whatever it described — a user could
        fix the `.env` the refusal named, press Refresh, and read "db up, auth
        up, world up" above "Nothing was stopped: this could equally be another
        install…" (review, 2026-08-22).
        """
        self._disarm_actions()
        self.problem_label.setText("")
        # Ask the database again: Refresh is the only way for a user who has
        # just fixed something to make the tab re-examine an unfinished import.
        self._import_asked = False
        self.refresh_status()

    @Slot(object)
    def _status_ready(self, result: object) -> None:
        self._status_pending = False
        status = result
        if not isinstance(status, InstallStatus):
            return
        if not self._busy:
            # Only while nothing of ours is running. The five-second poll used to
            # overwrite the label unconditionally, which was invisible at a
            # ten-second stop and is not at a five-minute one: the user pressed
            # Stop, read "stopping…", and then watched it revert to "world up"
            # for the next minute and a half with both buttons dead and no
            # explanation. The buttons below are still updated — it is the
            # sentence that has to hold still, not the state (review, 2026-08-23).
            parts = [
                f"db {'up' if status.db else 'down'}",
                f"auth {'up' if status.auth else 'down'}",
                f"world {'up' if status.world else 'down'}",
            ]
            self.status_label.setText("status: " + ", ".join(parts))
        self.start_button.setEnabled(not status.all_running and not self._busy)
        self.stop_button.setEnabled(status.any_running and not self._busy)
        self._ask_about_the_import(status)
        self.status_changed.emit(status)

    def _ask_about_the_import(self, status: InstallStatus) -> None:
        """Put the import question once per time the database comes up.

        Not on every poll: the probe is three `docker exec`s, and the poll runs
        every five seconds forever. Not never, either — the button has to be
        able to appear without the user knowing to press Refresh first, and the
        install this exists for is one whose Start visibly fails.
        """
        if not status.db:
            self._import_asked = False
            return
        if self._import_asked:
            return
        self._import_asked = True
        self._run(
            self.services.controller.import_state, self._import_state_ready, self._import_failed
        )

    @Slot(object)
    def _import_state_ready(self, result: object) -> None:
        if not isinstance(result, docker.ImportState):
            return
        self._import_state = result
        self._show_repair()

    @Slot(object)
    def _import_failed(self, exc: object) -> None:
        """A probe that raised says nothing about the database, so nothing is offered.

        `Controller.import_state()` is documented not to raise; this is the
        boundary that holds even if some future probe forgets, because the one
        outcome that must never follow from a failed question is a destructive
        button appearing.
        """
        logger.warning(f"could not ask the databases about their import: {exc}")
        self._import_state = None
        self._show_repair()

    def _show_repair(self) -> None:
        """Offer the repair only while the database itself says there is one to do."""
        state = self._import_state
        offer = state is not None and state.repairable
        self.repair_button.setVisible(offer)
        self.repair_label.setVisible(offer)
        if offer and state is not None:
            self.repair_label.setText(
                "This install's databases were never finished: "
                f"{state.detail}. The server will not start until the import is completed. "
                "Repair runs it again — see the button."
            )
        else:
            self._disarm_repair()
            self.repair_label.setText("")

    @Slot(object)
    def _status_failed(self, exc: object) -> None:
        self._status_pending = False
        self.status_label.setText(f"status: Docker not reachable ({exc})")

    def _set_busy(self, busy: bool) -> None:
        """Lock the Server buttons while an action of ours is running.

        All four, not two. Remove and Repair were left live while their own
        action ran, so a second arm-and-press during a multi-minute import or
        teardown started a second one on top of the first — and whichever
        finished first called `_set_busy(False)` and unlocked Start while the
        other was still writing schemas (review, 2026-08-23).
        """
        self._busy = busy
        if busy:
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            self.repair_button.setEnabled(False)
            # Refresh too, and this one is not symmetry. `recheck()` blanks
            # `problem_label` — which during an import is the live output the
            # user is watching — and then fires `Controller.import_state()`,
            # three `docker exec ... mysql` probes, at the database the import
            # is writing schemas into. Worse, the armed paragraph teaches
            # "press Refresh now", so it is the button a hesitating user
            # reaches for (review, 2026-08-23).
            self.refresh_button.setEnabled(False)
        else:
            self.refresh_button.setEnabled(True)
            # Re-enabled, not re-shown: `_show_repair()` owns whether Repair is
            # visible at all, and an invisible button being enabled is harmless.
            self.remove_button.setEnabled(True)
            self.repair_button.setEnabled(True)

    @Slot()
    def start_server(self) -> None:
        """Start the install; a README §12 conflict is shown, never a raw Docker error."""
        self._disarm_actions()
        self.problem_label.setText("")
        self._set_busy(True)
        self.status_label.setText("status: starting…")
        self._run(self.services.controller.start, self._server_action_done, self._start_failed)

    @Slot()
    def stop_server(self) -> None:
        self._disarm_actions()
        self.problem_label.setText("")
        self._set_busy(True)
        self.status_label.setText("status: stopping…")
        self._run(self.services.controller.stop, self._stop_done, self._stop_failed)

    @Slot(object)
    def _server_action_done(self, _result: object) -> None:
        self._set_busy(False)
        self.refresh_status()

    @Slot(object)
    def _stop_done(self, result: object) -> None:
        """Say so when the Stop found nothing to stop.

        `stop_staged()` distinguishes "this was running and is now down" from
        "there was nothing of it running"; the caller discarded that, so the
        button did the same thing either way and the tab could not tell the user
        which had happened (review, 2026-08-22).
        """
        self._set_busy(False)
        if result is False:
            self.problem_label.setText("None of this install's servers were running.")
        self.refresh_status()

    @Slot(object)
    def _start_failed(self, exc: object) -> None:
        self._set_busy(False)
        if isinstance(exc, PortConflictError):
            msg = (
                f"Another server is already using ports {exc.ports}: {', '.join(exc.containers)}. "
                "Stop it first — only one server can run at a time."
            )
        else:
            msg = str(exc)
        self.problem_label.setText(msg)
        self.action_failed.emit(msg)
        self.refresh_status()

    @Slot(object)
    def _stop_failed(self, exc: object) -> None:
        """Show why the stop refused. This used to emit into a signal nothing read.

        `stop_staged()` refuses rather than guess when it cannot prove it owns
        the containers, and says which project does own them and how to make the
        two agree. All of that was discarded: the status went "stopping…" and
        then straight back to "db up, auth up, world up", which is exactly what
        the silent bug it replaced looked like (review, 2026-08-22).
        """
        self._set_busy(False)
        msg = str(exc)
        self.problem_label.setText(msg)
        self.action_failed.emit(msg)
        self.refresh_status()

    @Slot()
    def remove_containers(self) -> None:
        """Arm on the first press; remove on the second.

        The action is safe for player data — the database is a named volume and
        `remove_staged()` never passes `-v` — but it is still a teardown, and it
        sits next to Stop. Arming says what will happen, in the same label the
        stop refusals use, before anything is touched.
        """
        if not self._remove_armed:
            # Only one of the two destructive buttons is ever armed. Both write
            # their warning into the same label, so two armed at once would show
            # one paragraph over two loaded buttons, and the second press would
            # do whichever the user had forgotten about.
            self._disarm_repair()
            self._remove_armed = True
            self.remove_button.setText(REMOVE_ARMED)
            self.problem_label.setText(
                "This stops the server and deletes its containers. Your characters are NOT "
                "affected — the database lives in a Docker volume, which is kept. The next "
                "Start recreates the containers, which takes longer than a normal start. "
                "Press Refresh to cancel."
            )
            return
        self._disarm_remove()
        self._set_busy(True)
        self.problem_label.setText("Removing containers…")
        self._run(self.services.controller.remove, self._remove_done, self._remove_failed)

    def _disarm_remove(self) -> None:
        self._remove_armed = False
        self.remove_button.setText(REMOVE_IDLE)

    def _disarm_repair(self) -> None:
        self._repair_armed = False
        self.repair_button.setText(REPAIR_IDLE)

    def _disarm_actions(self) -> None:
        """Any other server action means the user moved on from both of them."""
        self._disarm_remove()
        self._disarm_repair()

    @Slot(object)
    def _remove_done(self, result: object) -> None:
        self._set_busy(False)
        self.problem_label.setText(
            "Containers removed; volumes kept. The next Start will recreate them."
            if result
            else "There were no containers to remove."
        )
        self.refresh_status()

    @Slot(object)
    def _remove_failed(self, exc: object) -> None:
        self._set_busy(False)
        self.problem_label.setText(f"Could not remove the containers: {exc}")
        self.action_failed.emit(str(exc))

    @Slot()
    def repair_import(self) -> None:
        """Arm on the first press; re-run the one-shot import on the second.

        The armed paragraph says what is overwritten rather than what is kept —
        the opposite of the teardown's, and the honest way round. Everything the
        import writes is replaced, and the only reason this is offered at all is
        that the probe has already found no accounts and no characters to lose.
        `docker.repair_import()` asks the database again itself and refuses if
        that has changed since, so this text is a warning and not the guard.
        """
        if not self._repair_armed:
            self._disarm_remove()
            self._repair_armed = True
            self.repair_button.setText(REPAIR_ARMED)
            self.problem_label.setText(
                "This re-runs the database import that never finished. Everything in the auth, "
                "characters and world databases is OVERWRITTEN. It is offered because those "
                "databases hold no accounts and no characters — if that is wrong, press "
                "Refresh now, while nothing has happened yet, and restore a backup from the "
                "Maintenance tab instead. The server must be stopped; the database is started "
                "if it is not running and is left running afterwards.\n\n"
                "Press the button again to start. A full import takes 10-30 minutes, and once "
                "it starts it cannot be stopped and the window cannot be closed until it "
                "finishes."
            )
            return
        self._disarm_repair()
        self._set_busy(True)
        self._import_running = True
        self._import_tail.clear()
        # The offer described the state this run is in the middle of ending.
        # `_disarm_repair()` resets the flag and the button text and nothing
        # else, and `_show_repair()` is not reached again until the run
        # finishes — so "this install's databases were never finished" sat
        # directly under "Running the database import" for the whole 10-30
        # minutes, contradicting it (review, 2026-08-23).
        self.repair_label.setVisible(False)
        self.problem_label.setText(IMPORT_RUNNING)
        # The sink is the relay's emitter, not `_import_line`: this call runs on
        # a worker thread, and everything it invokes runs there too.
        self._run(
            lambda: self.services.controller.repair_import(self._import_relay.emit_line),
            self._repair_done,
            self._repair_failed,
        )

    @Slot(str)
    def _import_line(self, line: str) -> None:
        """Show the import's most recent output, so a long job cannot look like a hung one.

        Reached only through `_import_relay`, which is what puts it on the GUI
        thread. The whole log is deliberately NOT collected here: `docker
        compose logs ac-db-import` keeps it, `docker.run_attached()` retains a
        bounded tail for the failure message, and a half-hour of lines
        accumulating in a window that may stay open for days is the defect this
        change exists to avoid rather than one to introduce elsewhere.
        """
        text = line.strip()
        if not text:
            return
        if len(text) > _IMPORT_LINE_CHARS:
            text = text[:_IMPORT_LINE_CHARS] + "…"
        self._import_tail.append(text)
        self.problem_label.setText("\n".join([IMPORT_RUNNING, *self._import_tail]))

    @Slot(object)
    def _repair_done(self, _result: object) -> None:
        self._set_busy(False)
        self._import_running = False
        self.problem_label.setText(
            "The database import finished. Press Start — the server has a database to talk to now."
        )
        # The remembered answer is now stale in the one direction that matters:
        # leaving it would keep offering a repair for an install that has just
        # been repaired.
        self._import_state = None
        self._import_asked = False
        self._show_repair()
        self.refresh_status()

    @Slot(object)
    def _repair_failed(self, exc: object) -> None:
        self._set_busy(False)
        self._import_running = False
        self.problem_label.setText(str(exc))
        self.action_failed.emit(str(exc))
        self._import_asked = False
        self.refresh_status()

    # ----------------------------------------------------------- console tab

    def _build_console_tab(self) -> None:
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        self.console_log = LogPanel(tab)
        self.follow_button = QPushButton("Follow worldserver log", tab)
        self.follow_button.clicked.connect(self.follow_logs)
        self.command_edit = QLineEdit(tab)
        self.command_edit.setPlaceholderText("console command, e.g. server info")
        self.send_button = QPushButton("Send", tab)
        self.send_button.clicked.connect(self.send_console_command)
        cmd_row = QHBoxLayout()
        cmd_row.addWidget(self.command_edit, 1)
        cmd_row.addWidget(self.send_button)

        self.console_note = QLabel("", tab)
        self.console_note.setWordWrap(True)
        self.console_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.console_note.setVisible(False)
        if not wotlk_console.pty_supported():
            # Checklist 6.5 asks for this gap to be re-scoped, "not left silently
            # broken". Refusing on click and printing the error afterwards is not
            # the same as saying so up front: the catalog tile already disables
            # Install with the reason on the tile (6.1), so the console says it
            # the same way. Following the worldserver log needs no pty and stays
            # enabled, which is most of what this tab is for.
            self.send_button.setEnabled(False)
            self.command_edit.setEnabled(False)
            self.console_note.setText(
                wotlk_console.NO_TTY_HELP.format(container=self.entry.container_spec().world)
            )
            self.console_note.setVisible(True)

        box.addWidget(self.follow_button)
        box.addWidget(self.console_log, 1)
        box.addLayout(cmd_row)
        box.addWidget(self.console_note)
        self._tabs.addTab(tab, "Console")

    @Slot()
    def follow_logs(self) -> None:
        self.console_log.run(self.services.logs_source, title="worldserver log")

    @Slot()
    def send_console_command(self) -> None:
        command = self.command_edit.text().strip()
        if command:
            self._send(command)
            self.command_edit.clear()

    def _send(self, command: str) -> None:
        """Send one console command off the GUI thread (it waits for the reply window).

        Guarded the way `refresh_status()` is, and for a sharper reason. A
        command costs the whole 3s window whatever it answers, an empty answer
        is a routine outcome, and silence invites a second press — which used to
        start a SECOND `docker attach` on the same container and overwrite the
        pending callback. Two clients on one tty is what puts foreign prompts
        and echoes inside each other's windows (see `console._PROMPT`), so the
        obvious response to a quiet console was also the way to corrupt the next
        reply (review, 2026-08-23).

        It used to take a `then` callback so account creation could chain
        `account set gmlevel` behind `account create`. Nothing passes it any
        more — accounts have their own tab and write the row through SRP6 — so
        it went, along with a docstring that described a caller that no longer
        exists.
        """
        if self._console_pending:
            return
        shown = command if not command.startswith("account create") else "account create ****"
        self.console_log.append(f"> {shown}")
        self._console_pending = True
        self.send_button.setEnabled(False)
        self._run(
            lambda: self.services.send_console(command),
            self._console_reply,
            self._console_failed,
        )

    def _console_idle(self) -> None:
        """Re-arm Send — never where there is no pty (see `_build_console_tab()`)."""
        self._console_pending = False
        self.send_button.setEnabled(wotlk_console.pty_supported())

    @Slot(object)
    def _console_reply(self, result: object) -> None:
        self._console_idle()
        if not isinstance(result, wotlk_console.ConsoleReply):
            return
        if not result.prompted:
            # No `AC> ` anywhere in the window, so those lines are whatever
            # arrived rather than an answer. Docker's own failure looks like
            # this, and so does a worldserver still loading maps — which the tab
            # used to print as if it were a reply, into the panel that is
            # already streaming the same log.
            self.console_log.append(
                "(no console prompt in the reply window — what follows is whatever arrived, "
                "not an answer; the worldserver may still be starting)"
            )
        elif not result.lines:
            # Cutting between prompts makes an empty answer normal, and an empty
            # answer used to leave the user staring at their own echo with
            # nothing to distinguish it from a command the app had dropped.
            self.console_log.append("(no reply inside the 3s window)")
        for line in result.lines:
            self.console_log.append(line)

    @Slot(object)
    def _console_failed(self, exc: object) -> None:
        self._console_idle()
        self.console_log.append(f"!! {exc}")
        self.action_failed.emit(str(exc))

    # ----------------------------------------------------------- accounts tab

    def _build_accounts_tab(self) -> None:
        """Account creation, in its own tab because it no longer needs the console.

        It used to live under Console because it WAS the console: two commands
        typed down a `docker attach` pty. Writing the row directly means it works
        where there is no pty, which is every Windows box — so leaving it on a tab
        whose other controls are disabled there would hide the one thing that
        does work.
        """
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        accounts = QGroupBox("Create account", tab)
        form = QFormLayout(accounts)
        self.account_name = QLineEdit(accounts)
        self.account_password = QLineEdit(accounts)
        self.account_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.account_gm = QSpinBox(accounts)
        self.account_gm.setRange(0, 3)
        self.create_account_button = QPushButton("Create", accounts)
        self.create_account_button.clicked.connect(self.create_account)
        form.addRow("Username", self.account_name)
        form.addRow("Password", self.account_password)
        form.addRow("GM level", self.account_gm)
        form.addRow(self.create_account_button)

        self.account_report = QLabel("", tab)
        # A core this app cannot write an account for is said once, here, with
        # the command that does work — rather than left as a live button whose
        # every press ends in a SQL error, or worse in a row that inserts
        # cleanly and can never log in. See `catalog.Accounts`.
        if not self.entry.accounts.by_sql:
            self.create_account_button.setEnabled(False)
            for widget in (self.account_name, self.account_password, self.account_gm):
                widget.setEnabled(False)
            self.account_report.setText(
                f"{self.entry.name} keeps its accounts in a form this app does not write yet. "
                f"Make one on the Console tab instead: "
                f"{self.entry.accounts.console_command}"
            )
        self.account_report.setWordWrap(True)
        self.account_report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(accounts)
        box.addWidget(self.account_report)
        box.addStretch(1)
        self._tabs.addTab(tab, "Accounts")

    @Slot()
    def create_account(self) -> None:
        """Write the account row directly, rather than typing it at the console.

        This is the only way to make an account on a platform with no pty, and
        the only way to make the FIRST one anywhere — SOAP needs an account
        before it will authenticate, so it cannot bootstrap itself.
        """
        name = self.account_name.text().strip()
        password = self.account_password.text()
        if not name or not password:
            # The signal alone left the button doing nothing at all
            # (review, 2026-08-22), so the tab says it as well.
            self.account_report.setText("Username and password are required.")
            self.action_failed.emit("username and password are required")
            return
        gm_level = self.account_gm.value()
        self.account_report.setText(f"Creating {name}…")
        self.create_account_button.setEnabled(False)
        # The password is passed straight into the call and the field cleared; it
        # is never stored on the view, so no later repr or traceback frame of
        # this widget can carry it.
        self._run(
            lambda: self.services.create_account(name, password, gm_level),
            self._account_done,
            self._account_failed,
        )
        self.account_password.clear()

    @Slot(object)
    def _account_done(self, result: object) -> None:
        self.create_account_button.setEnabled(True)
        if not isinstance(result, wotlk_accounts.AccountResult):
            return
        made = "created" if result.created else "already existed"
        gm = f", GM level {result.gm_level}" if result.gm_level else ""
        self.account_report.setText(f"{result.username}: {made} (id {result.account_id}){gm}.")

    @Slot(object)
    def _account_failed(self, exc: object) -> None:
        self.create_account_button.setEnabled(True)
        self.account_report.setText(f"Could not create the account: {exc}")
        self.action_failed.emit(str(exc))

    # -------------------------------------------------------- maintenance tab

    def _build_maintenance_tab(self) -> None:
        """Backups, and a restore that cannot happen without its plan on screen.

        Deliberately shaped like the Networking tab (plan, then apply) rather
        than a confirmation dialog. A restore replaces every character on the
        server, so the thing being agreed to has to be readable while agreeing —
        `plan_restore()` collects every refusal instead of raising, precisely so
        all of them can be shown at once.
        """
        tab = QWidget(self)
        box = QVBoxLayout(tab)

        self.interrupted_label = QLabel("", tab)
        self.interrupted_label.setWordWrap(True)
        self.interrupted_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.forget_button = QPushButton("Forget that record", tab)
        self.forget_button.clicked.connect(self.forget_interrupted)
        self.interrupted_label.setVisible(False)
        self.forget_button.setVisible(False)

        top = QHBoxLayout()
        self.backup_button = QPushButton("Back up now", tab)
        self.refresh_backups_button = QPushButton("Refresh", tab)
        self.backup_button.clicked.connect(self.back_up)
        self.refresh_backups_button.clicked.connect(self.refresh_backups)
        top.addWidget(self.backup_button)
        top.addWidget(self.refresh_backups_button)
        top.addStretch(1)

        self.backup_list = QListWidget(tab)
        self.backup_list.currentItemChanged.connect(self._backup_selection_changed)

        actions = QHBoxLayout()
        self.plan_restore_button = QPushButton("Show restore plan", tab)
        self.restore_button = QPushButton("Restore", tab)
        self.plan_restore_button.clicked.connect(self.show_restore_plan)
        self.restore_button.clicked.connect(self.run_restore)
        # Never enabled by selecting a file: only a plan that came back allowed
        # turns this on, and changing the selection turns it off again.
        self.restore_button.setEnabled(False)
        actions.addWidget(self.plan_restore_button)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)

        self.maintenance_report = QPlainTextEdit(tab)
        self.maintenance_report.setReadOnly(True)

        box.addWidget(self.interrupted_label)
        box.addWidget(self.forget_button)
        box.addLayout(top)
        box.addWidget(self.backup_list, 2)
        box.addLayout(actions)
        box.addWidget(self.maintenance_report, 1)
        self._tabs.addTab(tab, "Maintenance")
        self.refresh_backups()

    def _selected_backup(self) -> Path | None:
        item = self.backup_list.currentItem()
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        return Path(str(path))

    @Slot(object, object)
    def _backup_selection_changed(self, _current: object, _previous: object) -> None:
        """A plan belongs to one file. Selecting another must not carry it over."""
        self._restore_plan = None
        self.restore_button.setEnabled(False)

    @Slot()
    def refresh_backups(self) -> None:
        """Re-list the backups directory. Reading a directory, not doing any work."""
        self._restore_plan = None
        self.restore_button.setEnabled(False)
        self.backup_list.clear()
        directory = self.services.backups_dir()
        for path in sorted(directory.glob("*.sql"), reverse=True):
            size = path.stat().st_size / (1024 * 1024)
            item = QListWidgetItem(f"{path.name}  ({size:.1f} MB)")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.backup_list.addItem(item)
        if self.backup_list.count() == 0:
            self.maintenance_report.setPlainText(f"No backups yet in {directory}.")
        self._show_interrupted()

    def _show_interrupted(self) -> None:
        """Surface a restore that never finished, and offer to put the record down."""
        record = self.services.interrupted_restore()
        if record is None:
            self.interrupted_label.setVisible(False)
            self.forget_button.setVisible(False)
            return
        if record.readable:
            named = ", ".join(record.databases) or "an unknown database"
            text = (
                f"A restore of {named} did not finish. Those databases may be half-written. "
                f"Restoring again is how that is escaped; the copy taken beforehand is "
                f"{', '.join(str(p) for p in record.safety_backup) or 'not recorded'}."
            )
        else:
            text = (
                f"There is a restore record at {record.marker} that cannot be read, so a restore "
                "was in flight but nothing about it can be established."
            )
        self.interrupted_label.setText(text)
        self.interrupted_label.setVisible(True)
        self.forget_button.setVisible(True)

    @Slot()
    def forget_interrupted(self) -> None:
        self._run(self.services.forget_interrupted, self._forget_done, self._maintenance_failed)

    @Slot(object)
    def _forget_done(self, _result: object) -> None:
        self._show_interrupted()

    @Slot()
    def back_up(self) -> None:
        self.backup_button.setEnabled(False)
        self.maintenance_report.setPlainText("Backing up… this can take minutes on a full world.")
        self._run(self.services.backup, self._backup_done, self._maintenance_failed)

    @Slot(object)
    def _backup_done(self, result: object) -> None:
        self.backup_button.setEnabled(True)
        if not isinstance(result, wotlk_maintenance.BackupReport):
            return
        lines = [f"Backed up to {result.directory}:"]
        lines += [
            f"  {d.database}  {d.size_bytes / (1024 * 1024):.1f} MB  {d.path.name}"
            for d in result.dumps
        ]
        if result.missing_core:
            lines.append(f"  !! expected but absent: {', '.join(result.missing_core)}")
        if result.server_was_running:
            lines.append("  note: the server was running, so this is a hot copy.")
        # Re-list BEFORE writing the report: refresh_backups() writes its own
        # message when the directory is empty, so refreshing afterwards wipes
        # the one thing the user just asked for (caught by its own test).
        self.refresh_backups()
        self.maintenance_report.setPlainText("\n".join(lines))

    @Slot()
    def show_restore_plan(self) -> None:
        path = self._selected_backup()
        if path is None:
            self.maintenance_report.setPlainText("Select a backup first.")
            return
        self._restore_plan = None
        self.restore_button.setEnabled(False)
        self._run(
            lambda: self.services.plan_restore(path),
            self._restore_plan_ready,
            self._maintenance_failed,
        )

    @Slot(object)
    def _restore_plan_ready(self, result: object) -> None:
        if not isinstance(result, wotlk_maintenance.RestorePlan):
            return
        lines = [
            f"Restoring {result.backup.name} would OVERWRITE: {', '.join(result.databases)}",
            f"  size: {result.size_bytes / (1024 * 1024):.1f} MB",
        ]
        if result.interrupted is not None and result.interrupted.readable:
            lines.append(
                "  an earlier restore of "
                f"{', '.join(result.interrupted.databases)} never finished"
            )
        if result.refusals:
            lines.append("")
            lines.append("This cannot go ahead:")
            lines += [f"  - {r}" for r in result.refusals]
        else:
            lines.append("")
            # Named from the plan rather than asserted. This said "Every
            # character on the server is replaced" on EVERY allowed plan — with
            # no check that `acore_characters` was even in it — so a world-only
            # restore threatened characters it would not touch, and the word
            # "replaced" was wrong besides: mysqldump emits `DROP TABLE IF
            # EXISTS` per table and no `DROP DATABASE`, so a restore MERGES
            # (measured on Windows, 2026-08-23: a table created after the backup
            # survived a full 306 MB restore of that schema). A warning that
            # overstates on one axis and understates on the other teaches the
            # user to discount it (review, 2026-08-24).
            named = ", ".join(result.databases) if result.databases else "nothing"
            lines.append(f"This overwrites: {named}.")
            lines.append(
                "Tables the backup does not contain are LEFT AS THEY ARE — a restore merges "
                "into the databases it names rather than returning them to the state the backup "
                "was taken from. Press Restore to go ahead."
            )
        self.maintenance_report.setPlainText("\n".join(lines))
        # Only a plan that is allowed arms the button, and only for this file.
        self._restore_plan = result if result.allowed else None
        self.restore_button.setEnabled(result.allowed)

    @Slot()
    def run_restore(self) -> None:
        plan = self._restore_plan
        if plan is None:
            # Belt and braces: the button is disabled without a plan, but a
            # restore is not something to leave to a widget's enabled state.
            self.maintenance_report.setPlainText("Show the restore plan first.")
            return
        self.restore_button.setEnabled(False)
        self.maintenance_report.setPlainText(f"Restoring {plan.backup.name}…")
        self._run(lambda: self.services.restore(plan), self._restore_done, self._maintenance_failed)

    @Slot(object)
    def _restore_done(self, result: object) -> None:
        self._restore_plan = None
        if not isinstance(result, wotlk_maintenance.RestoreReport):
            return
        safety = ", ".join(str(p) for p in result.safety_backup) or "none"
        # Re-list BEFORE writing the report: refresh_backups() writes its own
        # message when the directory is empty, so refreshing afterwards wipes
        # the one thing the user just asked for (caught by its own test).
        self.refresh_backups()
        self.maintenance_report.setPlainText(
            f"Restored {', '.join(result.databases)} from {result.backup}.\n"
            f"The copy taken beforehand: {safety}"
        )

    @Slot(object)
    def _maintenance_failed(self, exc: object) -> None:
        self.backup_button.setEnabled(True)
        self.maintenance_report.setPlainText(f"FAILED: {exc}")
        self.action_failed.emit(str(exc))
        self._show_interrupted()

    # ----------------------------------------------------------- modules tab

    def _build_modules_tab(self) -> None:
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        self.module_list = QListWidget(tab)
        self.module_report = QPlainTextEdit(tab)
        self.module_report.setReadOnly(True)
        self.install_module_button = QPushButton("Install selected", tab)
        self.remove_module_button = QPushButton("Remove selected", tab)
        self.install_module_button.clicked.connect(lambda: self._module_action("install"))
        self.remove_module_button.clicked.connect(lambda: self._module_action("remove"))
        row = QHBoxLayout()
        row.addWidget(self.install_module_button)
        row.addWidget(self.remove_module_button)
        box.addWidget(self.module_list, 2)
        box.addLayout(row)
        box.addWidget(self.module_report, 1)
        self._tabs.addTab(tab, "Modules")
        self._manifests: dict[str, Manifest] = {}
        self.reload_modules()
        enabled = self.services.store is not None and self.services.applier is not None
        self.install_module_button.setEnabled(enabled)
        self.remove_module_button.setEnabled(enabled)

    def reload_modules(self) -> None:
        """Fill the list from the store (every family), newest store contents first."""
        self.module_list.clear()
        self._manifests.clear()
        store = self.services.store
        if store is None:
            self.module_list.addItem("(this game has no manifests yet)")
            return
        for kind in FAMILY_FILES:
            try:
                items = list(store.load_all(kind))
            except Exception as exc:  # boundary: a broken manifest tree must not kill the UI
                self.module_list.addItem(f"!! could not load {kind}s: {exc}")
                continue
            for manifest in items:
                item = QListWidgetItem(
                    f"[{manifest.type}] {manifest.name} — {manifest.description}"
                )
                item.setData(256, manifest.id)  # Qt.UserRole
                self.module_list.addItem(item)
                self._manifests[manifest.id] = manifest

    def selected_manifest(self) -> Manifest | None:
        item = self.module_list.currentItem()
        if item is None:
            return None
        return self._manifests.get(str(item.data(256)))

    def _module_action(self, action: str) -> None:
        manifest = self.selected_manifest()
        applier = self.services.applier
        if manifest is None or applier is None:
            return
        run = applier.install if action == "install" else applier.remove
        self._module_pending = f"{action} {manifest.id}"
        self.module_report.setPlainText(f"{self._module_pending}…")
        self._run(lambda: run(manifest), self._module_done, self._module_failed)

    @Slot(object)
    def _module_done(self, result: object) -> None:
        self._module_pending = None
        if isinstance(result, ApplyReport):
            self.module_report.setPlainText(_format_report(result))

    @Slot(object)
    def _module_failed(self, exc: object) -> None:
        what, self._module_pending = self._module_pending or "module action", None
        self.module_report.setPlainText(f"{what} FAILED: {exc}")
        self.action_failed.emit(str(exc))

    # -------------------------------------------------------- networking tab

    def _build_networking_tab(self) -> None:
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        self.lan_radio = QRadioButton("LAN (same Wi-Fi)", tab)
        self.internet_radio = QRadioButton("Internet play (friends elsewhere)", tab)
        self.lan_radio.setChecked(True)
        group = QButtonGroup(tab)
        group.addButton(self.lan_radio)
        group.addButton(self.internet_radio)
        self.plan_button = QPushButton("Show plan", tab)
        self.apply_button = QPushButton("Apply", tab)
        self.apply_button.setEnabled(False)
        self.plan_button.clicked.connect(self.show_network_plan)
        self.apply_button.clicked.connect(self.apply_network_plan)
        self.network_text = QPlainTextEdit(tab)
        self.network_text.setReadOnly(True)
        row = QHBoxLayout()
        row.addWidget(self.lan_radio)
        row.addWidget(self.internet_radio)
        row.addStretch(1)
        row.addWidget(self.plan_button)
        row.addWidget(self.apply_button)
        box.addLayout(row)
        box.addWidget(self.network_text, 1)
        self._tabs.addTab(tab, "Networking")
        self._plan: NetworkPlan | None = None

    def network_mode(self) -> Mode:
        return "internet" if self.internet_radio.isChecked() else "lan"

    @Slot()
    def show_network_plan(self) -> None:
        mode = self.network_mode()
        self.network_text.setPlainText("working out the plan… (this can take a few seconds)")
        self._run(lambda: self.services.network_plan(mode), self._plan_ready, self._plan_failed)

    @Slot(object)
    def _plan_ready(self, result: object) -> None:
        if not isinstance(result, NetworkPlan):
            return
        self._plan = result
        self.network_text.setPlainText(_format_plan(result))
        self.apply_button.setEnabled(result.ready)

    @Slot(object)
    def _plan_failed(self, exc: object) -> None:
        self.network_text.setPlainText(f"could not plan: {exc}")
        self.action_failed.emit(str(exc))

    @Slot()
    def apply_network_plan(self) -> None:
        plan = self._plan
        if plan is None:
            return
        self.apply_button.setEnabled(False)
        self._run(lambda: self.services.network_apply(plan), self._apply_done, self._apply_failed)

    @Slot(object)
    def _apply_done(self, result: object) -> None:
        if isinstance(result, NetworkReport):
            self.network_text.appendPlainText("\n" + _format_network_report(result))
        self.apply_button.setEnabled(True)

    @Slot(object)
    def _apply_failed(self, exc: object) -> None:
        self.network_text.appendPlainText(f"\nAPPLY FAILED: {exc}")
        self.action_failed.emit(str(exc))
        self.apply_button.setEnabled(True)


# ------------------------------------------------------------- formatting


def _format_report(report: ApplyReport) -> str:
    lines = [f"{report.action} {report.item_id}:"]
    lines += [f"  ✓ {step}" for step in report.done]
    lines += [f"  – skipped: {step}" for step in report.skipped]
    if report.rebuild_required:
        lines.append("  ⚠ worldserver REBUILD required before this takes effect")
    elif report.restart_recommended:
        lines.append("  ⚠ restart the server to apply")
    return "\n".join(lines)


def _format_plan(plan: NetworkPlan) -> str:
    lines = [
        f"Mode: {plan.mode}   LAN IP: {plan.lan_ip or '?'}   public IP: {plan.public_ip or '-'}",
        f"Ports: {', '.join(map(str, plan.ports))}   firewall: {plan.firewall}",
    ]
    if plan.client_realmlist:
        lines.append(f"Players set realmlist to: {plan.client_realmlist}")
    if plan.firewall_commands:
        lines.append("Firewall commands:")
        lines += ["  " + " ".join(c) for c in plan.firewall_commands]
    if plan.portproxy_commands:
        lines.append("Port proxy commands:")
        lines += ["  " + " ".join(c) for c in plan.portproxy_commands]
    if plan.realmlist_sql:
        lines.append(f"Realmlist: {plan.realmlist_sql}")
    if plan.warnings:
        lines.append("Warnings:")
        lines += [f"  ⚠ {w}" for w in plan.warnings]
    if plan.manual_steps:
        lines.append("You need to do these yourself:")
        lines += [f"  {i}. {s}" for i, s in enumerate(plan.manual_steps, 1)]
    if not plan.ready:
        lines.append("Not ready to apply — see warnings.")
    return "\n".join(lines)


def _format_network_report(report: NetworkReport) -> str:
    lines = ["Applied:"]
    lines += [f"  ✓ {d}" for d in report.done] or ["  (nothing)"]
    if report.skipped:
        lines.append("Could not do (run by hand):")
        lines += [f"  – {s}" for s in report.skipped]
    if report.restart_required:
        lines.append("⚠ restart the server so the new realmlist address is used")
    return "\n".join(lines)
