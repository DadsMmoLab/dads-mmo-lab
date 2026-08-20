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

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer, Signal, Slot
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

from yulon import docker, networking, runner
from yulon.apply import Applier, ApplyReport, DockerSql
from yulon.catalog.catalog import CatalogEntry
from yulon.controller import Controller, PortConflictError
from yulon.controller_wow_wotlk import console as wotlk_console
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.log import get_logger
from yulon.manifest import Manifest
from yulon.manifest_store import FAMILY_FILES, ManifestStore
from yulon.networking import Mode, NetworkPlan, NetworkReport
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

    @classmethod
    def for_wotlk(
        cls, entry: CatalogEntry, server_dir: Path, client_dir: Path | None = None
    ) -> ControllerServices:
        """The real WotLK wiring for an install at `server_dir`."""
        spec = entry.container_spec()
        controller = Controller(spec, server_dir)
        password = entry.install.db_root_password or wotlk_modules.DEFAULT_DB_ROOT_PASSWORD
        sql = DockerSql(spec.db, password)
        return cls(
            controller=controller,
            logs_source=lambda: runner.stream(
                ["docker", "logs", "-f", "--tail", "200", spec.world]
            ),
            send_console=lambda cmd: wotlk_console.send_command(cmd, container=spec.world),
            store=wotlk_modules.store() if entry.has_manifests else None,
            applier=(
                wotlk_modules.applier(server_dir, sql=sql, client_dir=client_dir)
                if entry.has_manifests
                else None
            ),
            network_plan=lambda mode: networking.plan(entry, mode, bindings=_safe_bindings()),
            network_apply=lambda plan: networking.apply(plan, sql=sql),
        )


def _safe_bindings() -> dict[int, str] | None:
    try:
        return docker.published_bindings()
    except docker.DockerCommandError:
        return None


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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.services = services
        self._tabs = QTabWidget(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)

        self._build_server_tab()
        self._build_console_tab()
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
        self.conflict_label = QLabel("", tab)
        self.conflict_label.setWordWrap(True)
        self.start_button = QPushButton("Start", tab)
        self.stop_button = QPushButton("Stop", tab)
        self.refresh_button = QPushButton("Refresh", tab)
        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.refresh_button.clicked.connect(self.refresh_status)
        row = QHBoxLayout()
        for b in (self.start_button, self.stop_button, self.refresh_button):
            row.addWidget(b)
        box.addWidget(QLabel(f"<b>{self.entry.name}</b> — {self.services.controller.server_dir}"))
        box.addWidget(self.status_label)
        box.addLayout(row)
        box.addWidget(self.conflict_label)
        box.addStretch(1)
        self._tabs.addTab(tab, "Server")

    @Slot()
    def refresh_status(self) -> None:
        """Re-read `docker ps` and update the Server tab."""
        try:
            status = self.services.controller.status()
        except docker.DockerCommandError as exc:
            self.status_label.setText(f"status: Docker not reachable ({exc})")
            return
        parts = [
            f"db {'up' if status.db else 'down'}",
            f"auth {'up' if status.auth else 'down'}",
            f"world {'up' if status.world else 'down'}",
        ]
        self.status_label.setText("status: " + ", ".join(parts))
        self.start_button.setEnabled(not status.all_running)
        self.stop_button.setEnabled(status.any_running)
        self.status_changed.emit(status)

    @Slot()
    def start_server(self) -> None:
        """Start the install; a README §12 conflict is shown, never a raw Docker error."""
        try:
            self.services.controller.start()
            self.conflict_label.setText("")
        except PortConflictError as exc:
            msg = (
                f"Another server is already using ports {exc.ports}: {', '.join(exc.containers)}. "
                "Stop it first — only one server can run at a time."
            )
            self.conflict_label.setText(msg)
            self.action_failed.emit(msg)
        except docker.DockerCommandError as exc:
            self.conflict_label.setText(str(exc))
            self.action_failed.emit(str(exc))
        self.refresh_status()

    @Slot()
    def stop_server(self) -> None:
        try:
            self.services.controller.stop()
        except docker.DockerCommandError as exc:
            self.action_failed.emit(str(exc))
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

        box.addWidget(self.follow_button)
        box.addWidget(self.console_log, 1)
        box.addLayout(cmd_row)
        box.addWidget(accounts)
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

    def _send(self, command: str) -> wotlk_console.ConsoleReply | None:
        try:
            reply = self.services.send_console(command)
        except (wotlk_console.ConsoleError, ValueError) as exc:
            self.console_log.append(f"!! {exc}")
            self.action_failed.emit(str(exc))
            return None
        shown = command if not command.startswith("account create") else "account create ****"
        self.console_log.append(f"> {shown}")
        for line in reply.lines:
            self.console_log.append(line)
        return reply

    @Slot()
    def create_account(self) -> None:
        """`account create <name> <password>` then `account set gmlevel` when > 0."""
        name = self.account_name.text().strip()
        password = self.account_password.text()
        if not name or not password:
            self.action_failed.emit("username and password are required")
            return
        if self._send(f"account create {name} {password}") is None:
            return
        if self.account_gm.value() > 0:
            self._send(f"account set gmlevel {name} {self.account_gm.value()} -1")
        self.account_password.clear()

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
        try:
            report = run(manifest)
        except Exception as exc:  # boundary: show, never crash the window
            self.module_report.setPlainText(f"{action} {manifest.id} FAILED: {exc}")
            self.action_failed.emit(str(exc))
            return
        self.module_report.setPlainText(_format_report(report))

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
        try:
            self._plan = self.services.network_plan(self.network_mode())
        except Exception as exc:  # boundary
            self.network_text.setPlainText(f"could not plan: {exc}")
            self.action_failed.emit(str(exc))
            return
        self.network_text.setPlainText(_format_plan(self._plan))
        self.apply_button.setEnabled(self._plan.ready)

    @Slot()
    def apply_network_plan(self) -> None:
        if self._plan is None:
            return
        try:
            report = self.services.network_apply(self._plan)
        except Exception as exc:  # boundary
            self.network_text.appendPlainText(f"\nAPPLY FAILED: {exc}")
            self.action_failed.emit(str(exc))
            return
        self.network_text.appendPlainText("\n" + _format_network_report(report))


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
