"""Tests for `ControllerView` (roadmap 4.3) through `ControllerServices` fakes, offscreen."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import docker, networking, runner
from yulon.apply import Applier, ApplyReport
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller
from yulon.controller_wow_wotlk import console, modules
from yulon.controller_wow_wotlk.console import ConsoleReply
from yulon.networking import NetworkPlan, NetworkReport
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the view's background jobs synchronously.

    In the app every service call goes to a worker thread (that is the point —
    the window must not freeze); in tests the same calls run inline so a click's
    effect is visible on the next line.
    """
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


class _Ps:
    """Fakes `runner.run` for `docker ps`/compose so `Controller` works without Docker."""

    def __init__(self) -> None:
        self.names = ""
        self.ports = ""
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            out = self.ports if "{{.Ports}}" in cmd[-1] else self.names
            return subprocess.CompletedProcess(cmd, 0, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


class _FakeApplier(Applier):
    def __init__(self) -> None:
        super().__init__(Path("/srv"), git=None)  # type: ignore[arg-type]
        self.installed: list[str] = []

    def install(self, manifest: object, values: object = None) -> ApplyReport:  # type: ignore[override]
        item_id = str(manifest.id)  # type: ignore[attr-defined]
        self.installed.append(item_id)
        return ApplyReport("install", item_id, done=("clone",), rebuild_required=True)


def _services(ps: _Ps, tmp_path: Path, sent: list[str]) -> ControllerServices:
    plan = networking.plan(
        WOTLK, "lan", lan_ip="192.168.1.25", firewall="none", steamos=False, wsl=False
    )

    def send(cmd: str) -> ConsoleReply:
        sent.append(cmd)
        return ConsoleReply(cmd, ("ok",))

    def logs() -> Iterator[str]:
        yield "world log line"

    return ControllerServices(
        controller=Controller(WOTLK.container_spec(), tmp_path),
        logs_source=logs,
        send_console=send,
        store=modules.store(),
        applier=_FakeApplier(),
        network_plan=lambda mode: plan,
        network_apply=lambda p: NetworkReport(
            p, done=("realmlist → 192.168.1.25",), restart_required=True
        ),
    )


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def test_server_tab_status_start_and_port_conflict_message(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    sent: list[str] = []
    view = ControllerView(WOTLK, _services(ps, tmp_path, sent), status_poll_ms=0)
    ps.names = "ac-database\n"
    view.refresh_status()
    assert "db up, auth down, world down" in view.status_label.text()
    assert view.start_button.isEnabled() and view.stop_button.isEnabled()

    # A foreign container on 3724 → README §12 message, compose up never runs.
    ps.ports = "tbc-realmd\t0.0.0.0:3724->3724/tcp\n"
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.start_server()
    assert "only one server can run at a time" in view.conflict_label.text()
    assert "tbc-realmd" in failures[0]
    assert ["docker", "compose", "up", "-d"] not in ps.calls

    ps.ports = ""
    view.start_server()
    assert ["docker", "compose", "up", "-d"] in ps.calls
    assert view.conflict_label.text() == ""
    view.stop_server()
    # Stop keeps the containers (`compose stop`), so the next start stays staged.
    assert ["docker", "compose", "stop"] in ps.calls
    assert ["docker", "compose", "down"] not in ps.calls


def test_console_tab_sends_commands_and_creates_accounts(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    sent: list[str] = []
    view = ControllerView(WOTLK, _services(ps, tmp_path, sent), status_poll_ms=0)
    view.command_edit.setText("server info")
    view.send_console_command()
    assert sent == ["server info"]
    assert "> server info" in view.console_log.text() and "ok" in view.console_log.text()

    view.account_name.setText("dad")
    view.account_password.setText("s3cret")
    view.account_gm.setValue(3)
    view.create_account()
    assert sent[1:] == ["account create dad s3cret", "account set gmlevel dad 3 -1"]
    assert "s3cret" not in view.console_log.text()  # password never echoed
    assert view.account_password.text() == ""


def test_modules_tab_lists_manifests_and_installs_selected(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.module_list.count() >= 40
    for i in range(view.module_list.count()):
        if view.module_list.item(i).data(256) == "mod-ah-bot":
            view.module_list.setCurrentRow(i)
            break
    assert view.selected_manifest() is not None and view.selected_manifest().id == "mod-ah-bot"
    view._module_action("install")
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier) and applier.installed == ["mod-ah-bot"]
    assert "REBUILD required" in view.module_report.toPlainText()


def test_networking_tab_plans_and_applies(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.network_mode() == "lan"
    assert view.apply_button.isEnabled() is False
    view.show_network_plan()
    text = view.network_text.toPlainText()
    assert "Players set realmlist to: 192.168.1.25" in text
    assert "allow inbound TCP 3724, 8085 by hand" in text  # firewall=none → manual step
    assert view.apply_button.isEnabled() is True
    view.apply_network_plan()
    assert "realmlist → 192.168.1.25" in view.network_text.toPlainText()
    assert "restart the server" in view.network_text.toPlainText()


def test_for_wotlk_builds_real_services_without_touching_docker(tmp_path: Path) -> None:
    services = ControllerServices.for_wotlk(WOTLK, tmp_path, None)
    assert services.controller.spec == WOTLK.container_spec()
    assert services.store is not None and services.applier is not None
    assert isinstance(services.network_plan, type(lambda: None))
    # NetworkPlan/docker are only touched when the callables run.
    assert isinstance(NetworkPlan, type) and docker.ContainerSpec is not None
    assert console.attach_argv("ac-worldserver")[:2] == ["docker", "attach"]
