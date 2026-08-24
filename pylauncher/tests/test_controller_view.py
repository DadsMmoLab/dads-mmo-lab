"""Tests for `ControllerView` (roadmap 4.3) through `ControllerServices` fakes, offscreen."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from yulon import docker, networking, runner
from yulon.apply import Applier, ApplyReport
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller
from yulon.controller_wow_wotlk import console, modules
from yulon.controller_wow_wotlk.accounts import AccountResult
from yulon.controller_wow_wotlk.console import ConsoleReply
from yulon.controller_wow_wotlk.maintenance import (
    BackupReport,
    InterruptedRestore,
    RestorePlan,
    RestoreReport,
)
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
        # What `docker compose config` says this folder's project is called...
        self.project = "t-project"
        # ...and what the running containers are actually labelled with. Equal
        # in the ordinary case; a test makes them disagree to model a second
        # install of the same game, whose container names are identical.
        self.label: str | None = None

    def __call__(
        self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            out = self.ports if "{{.Ports}}" in cmd[-1] else self.names
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return subprocess.CompletedProcess(cmd, 0, '{"name": "' + self.project + '"}', "")
        if cmd[:3] == ["docker", "compose", "stop"]:
            self.names = ""  # compose really stopped them
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:5] == ["docker", "compose", "up", "-d", "--no-deps"]:
            # `start_staged()` confirms with `docker ps` that the services it
            # named really came up; whatever it asked for is what appears.
            self.names = "".join(f"{name}\n" for name in cmd[5:])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["docker", "inspect"] and any(docker.PROJECT_LABEL in a for a in cmd):
            owner = self.label if self.label is not None else self.project
            return subprocess.CompletedProcess(cmd, 0, owner + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


class _FakeApplier(Applier):
    def __init__(self) -> None:
        super().__init__(Path("/srv"), git=None)  # type: ignore[arg-type]
        self.installed: list[str] = []

    def install(self, manifest: object, values: object = None) -> ApplyReport:  # type: ignore[override]
        item_id = str(manifest.id)  # type: ignore[attr-defined]
        self.installed.append(item_id)
        return ApplyReport("install", item_id, done=("clone",), rebuild_required=True)


class _FakeMaintenance:
    """Stands in for `maintenance` and `accounts` in the view tests.

    The view must not do any of this work itself (style-guide §3), so every one
    of these is a seam it calls down into. Recording the calls is how the tests
    check that a restore cannot happen without a plan first.
    """

    def __init__(self) -> None:
        self.created: list[tuple[str, str, int]] = []
        self.backups = 0
        self.planned: list[Path] = []
        self.restored: list[RestorePlan] = []
        self.forgotten = 0
        self.interrupted: InterruptedRestore | None = None
        self.refusals: tuple[str, ...] = ()

    def create(self, name: str, password: str, gm: int) -> AccountResult:
        self.created.append((name, password, gm))
        return AccountResult(username=name, account_id=12401, created=True, gm_level=gm)

    def back_up(self) -> BackupReport:
        self.backups += 1
        return BackupReport(directory=Path("backups"), dumps=())

    def plan(self, path: Path) -> RestorePlan:
        self.planned.append(path)
        return RestorePlan(
            backup=path,
            server_dir=path.parent,
            databases=("acore_characters",),
            size_bytes=2048,
            refusals=self.refusals,
        )

    def do_restore(self, plan: RestorePlan) -> RestoreReport:
        self.restored.append(plan)
        return RestoreReport(backup=plan.backup, databases=plan.databases, safety_backup=())

    def forget(self) -> bool:
        self.forgotten += 1
        self.interrupted = None
        return True


def _services(
    ps: _Ps, tmp_path: Path, sent: list[str], made: _FakeMaintenance | None = None
) -> ControllerServices:
    plan = networking.plan(
        WOTLK, "lan", lan_ip="192.168.1.25", firewall="none", steamos=False, wsl=False
    )

    def send(cmd: str) -> ConsoleReply:
        sent.append(cmd)
        return ConsoleReply(cmd, ("ok",))

    def logs() -> Iterator[str]:
        yield "world log line"

    made = made if made is not None else _FakeMaintenance()
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
        create_account=made.create,
        backup=made.back_up,
        backups_dir=lambda: tmp_path / "sql_scripts" / "backups",
        plan_restore=made.plan,
        restore=made.do_restore,
        interrupted_restore=lambda: made.interrupted,
        forget_interrupted=made.forget,
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
    assert "only one server can run at a time" in view.problem_label.text()
    assert "tbc-realmd" in failures[0]
    assert not any(c[:4] == ["docker", "compose", "up", "-d"] for c in ps.calls)

    ps.ports = ""
    view.start_server()
    assert any(c[:5] == ["docker", "compose", "up", "-d", "--no-deps"] for c in ps.calls)
    assert view.problem_label.text() == ""
    view.stop_server()
    # Stop keeps the containers (`compose stop`), so the next start stays staged.
    assert any(c[:3] == ["docker", "compose", "stop"] for c in ps.calls)
    assert not any(
        cmd[:3] == ["docker", "compose", "down"] for cmd in ps.calls
    ), "a stop removed containers"


def test_a_refused_stop_is_readable_on_screen_not_just_emitted(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A stop that refuses must say so where the user is looking.

    `stop_staged()` refuses rather than guess when the running containers carry
    another compose project's label — two installs of one game share container
    names exactly, so stopping the wrong one takes down somebody's server. That
    refusal used to be emitted into `action_failed` and read by nobody: the
    label went "stopping…" then back to "db up", which is indistinguishable
    from the silent bug the refusal exists to prevent (review, 2026-08-22).
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    ps.names = "ac-database\nac-authserver\nac-worldserver\n"
    ps.label = "somebody-elses-install"  # the containers disagree with our own project

    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.stop_server()

    shown = view.problem_label.text()
    assert "do not belong to the install" in shown, f"the refusal was not shown: {shown!r}"
    assert "somebody-elses-install" in shown, "did not name the project that does own them"
    assert "COMPOSE_PROJECT_NAME=somebody-elses-install" in shown, "did not name the remedy"
    assert failures and failures[0] == shown
    assert ["docker", "compose", "stop"] not in ps.calls
    assert not any(c[:2] == ["docker", "stop"] for c in ps.calls), "stopped a foreign server"


def _add_backup(view: ControllerView, tmp_path: Path, name: str = "chars.sql") -> None:
    """Put a file where `backups_dir()` points and re-list, then select it."""
    directory = tmp_path / "sql_scripts" / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(b"-- dump\n")
    view.refresh_backups()
    view.backup_list.setCurrentRow(0)


def test_missing_account_fields_say_so_on_the_accounts_tab(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Pressing Create with an empty form used to do nothing visible at all."""
    made = _FakeMaintenance()
    view = ControllerView(WOTLK, _services(ps, tmp_path, [], made), status_poll_ms=0)
    view.account_name.setText("")
    view.account_password.setText("")
    view.create_account()
    assert "required" in view.account_report.text()
    assert made.created == []


def test_console_tab_sends_commands(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    sent: list[str] = []
    view = ControllerView(WOTLK, _services(ps, tmp_path, sent), status_poll_ms=0)
    view.command_edit.setText("server info")
    view.send_console_command()
    assert sent == ["server info"]
    assert "> server info" in view.console_log.text() and "ok" in view.console_log.text()


def test_an_empty_reply_is_said_out_loud_rather_than_shown_as_silence(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Cutting between prompts makes `()` routine; silence reads as a dropped command.

    Before the parser delimited anything an empty reply was near impossible on a
    busy server — the old window always carried something back. Now a command
    with no output, or one whose answer outlived the window, ends here, and the
    user was left staring at their own echo with nothing to act on.
    """
    services = _services(ps, tmp_path, [])
    services.send_console = lambda cmd: ConsoleReply(cmd, ())
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view.command_edit.setText("gm list")
    view.send_console_command()
    assert "no reply inside the 3s window" in view.console_log.text()


def test_a_window_with_no_prompt_is_not_presented_as_an_answer(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A worldserver still loading maps prints no `AC> ` — and Send is live throughout.

    Those lines are the startup log, which this same panel already streams. They
    are still shown, because docker's own failure arrives in exactly this shape
    and hiding it would turn the one explanation into silence — but they are no
    longer shown as the command's reply.
    """
    services = _services(ps, tmp_path, [])
    services.send_console = lambda cmd: ConsoleReply(cmd, ("Loading maps 12%",), prompted=False)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view.command_edit.setText("gm list")
    view.send_console_command()
    text = view.console_log.text()
    assert "no console prompt in the reply window" in text
    assert "Loading maps 12%" in text


def test_send_refuses_a_second_command_while_one_is_in_flight(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A second attach client on one tty corrupts both windows (see `console._PROMPT`).

    Nothing answers for three seconds, so pressing Send again is the natural
    thing to do; it used to start a concurrent `docker attach` and overwrite the
    pending callback.
    """
    pending: list[object] = []

    def never_finishes(work: object, on_done: object, on_error: object) -> None:
        pending.append(work)

    view = ControllerView(
        WOTLK, _services(ps, tmp_path, []), status_poll_ms=0, job_runner=never_finishes
    )
    view.command_edit.setText("server info")
    view.send_console_command()
    view.command_edit.setText("gm list")
    view.send_console_command()
    assert len(pending) == 1, "a second attach was started while the first was still open"
    assert not view.send_button.isEnabled()
    assert "gm list" not in view.console_log.text()


def test_creating_an_account_never_touches_the_console(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The point of the SRP6 path: it works where `docker attach` cannot.

    It used to be two commands typed at the console, which needs a pty, which
    Windows does not have — so on Windows an account could not be created at
    all. Nothing may reach `send_console` here, and the password must not be
    left in the field or echoed into the log.
    """
    sent: list[str] = []
    made = _FakeMaintenance()
    view = ControllerView(WOTLK, _services(ps, tmp_path, sent, made), status_poll_ms=0)
    view.account_name.setText("dad")
    view.account_password.setText("s3cret")
    view.account_gm.setValue(3)
    view.create_account()

    assert made.created == [("dad", "s3cret", 3)]
    assert sent == [], "account creation went through the console"
    assert view.account_password.text() == ""
    assert "s3cret" not in view.console_log.text()
    assert "s3cret" not in view.account_report.text()
    assert "dad" in view.account_report.text()


def test_the_console_says_why_it_is_disabled_where_there_is_no_pty(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checklist 6.5 asks for this gap re-scoped, "not left silently broken".

    Refusing on click and printing the error afterwards leaves a button that
    looks usable. Following the log needs no pty and stays enabled, which is
    what makes disabling the rest honest rather than punitive.
    """
    monkeypatch.setattr(console, "pty_supported", lambda: False)
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert not view.send_button.isEnabled()
    assert not view.command_edit.isEnabled()
    assert view.console_note.isVisible() or view.console_note.text()
    assert "terminal" in view.console_note.text()
    assert view.follow_button.isEnabled(), "following the log needs no pty"


def test_a_restore_will_not_run_without_a_plan(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The button is disabled, and the slot refuses anyway.

    A restore replaces every character on the server, so "the widget was
    disabled" is not the only thing standing between a click and that.
    """
    made = _FakeMaintenance()
    view = ControllerView(WOTLK, _services(ps, tmp_path, [], made), status_poll_ms=0)
    assert not view.restore_button.isEnabled()
    view.run_restore()
    assert made.restored == []


def test_a_refused_plan_never_arms_the_restore_button(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Every refusal is shown at once, and none of them is dismissible by clicking."""
    made = _FakeMaintenance()
    made.refusals = ("the worldserver is running", "the database container is not up")
    view = ControllerView(WOTLK, _services(ps, tmp_path, [], made), status_poll_ms=0)
    _add_backup(view, tmp_path)

    view.show_restore_plan()
    assert not view.restore_button.isEnabled()
    assert "the worldserver is running" in view.maintenance_report.toPlainText()
    assert "the database container is not up" in view.maintenance_report.toPlainText()

    view.run_restore()
    assert made.restored == []


def test_choosing_a_different_backup_forgets_the_plan(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A plan belongs to one file; carrying it over would restore the wrong one."""
    made = _FakeMaintenance()
    view = ControllerView(WOTLK, _services(ps, tmp_path, [], made), status_poll_ms=0)
    _add_backup(view, tmp_path, "a.sql")
    _add_backup(view, tmp_path, "b.sql")

    view.backup_list.setCurrentRow(0)
    view.show_restore_plan()
    assert view.restore_button.isEnabled()

    view.backup_list.setCurrentRow(1)
    assert not view.restore_button.isEnabled()
    view.run_restore()
    assert made.restored == []


def test_a_planned_restore_runs_and_reports(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    made = _FakeMaintenance()
    view = ControllerView(WOTLK, _services(ps, tmp_path, [], made), status_poll_ms=0)
    _add_backup(view, tmp_path)

    view.show_restore_plan()
    view.run_restore()
    assert [p.backup.name for p in made.restored] == ["chars.sql"]
    assert "acore_characters" in view.maintenance_report.toPlainText()


def test_backing_up_says_where_it_went(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    made = _FakeMaintenance()
    view = ControllerView(WOTLK, _services(ps, tmp_path, [], made), status_poll_ms=0)
    view.back_up()
    assert made.backups == 1
    assert "Backed up to" in view.maintenance_report.toPlainText()


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


def test_a_stop_with_nothing_running_says_so(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Stop on an already-stopped install used to look identical to a real stop."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    ps.names = ""  # nothing of ours is up
    view.stop_server()
    assert "None of this install's servers were running" in view.problem_label.text()


def _watch_remove(view: ControllerView, result: bool = True) -> list[int]:
    """Replace the controller's teardown with a recorder.

    The view's job here is the arming, not the removal; `remove_staged()` has
    its own tests in test_docker.py, including the mutation that would add the
    `-v` this button must never cause.
    """
    calls: list[int] = []

    def fake_remove() -> bool:
        calls.append(1)
        return result

    view.services.controller.remove = fake_remove  # type: ignore[method-assign]
    return calls


def test_removing_containers_takes_two_presses(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """A teardown sitting next to Stop must not be one click away."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    calls = _watch_remove(view)

    view.remove_containers()
    assert calls == [], "the first press removed something"
    assert view.remove_button.text() == controller_view_module.REMOVE_ARMED

    view.remove_containers()
    assert calls == [1]
    assert view.remove_button.text() == controller_view_module.REMOVE_IDLE, "still armed after"


def test_the_armed_warning_says_the_characters_are_kept(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The reason this action is safe is the reason it must be stated.

    Someone reading "delete containers" next to a server they have played on
    will assume the worst unless told otherwise, and the truth — the database is
    a volume and volumes are kept — is exactly what makes it pressable.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _watch_remove(view)
    view.remove_containers()
    said = view.problem_label.text()
    assert "NOT" in said and "characters" in said
    assert "volume" in said
    assert "Refresh" in said, "no way out was offered"


def test_refresh_cancels_an_armed_remove(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Arming then walking away must not leave a loaded button behind."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    calls = _watch_remove(view)

    view.remove_containers()
    assert view.remove_button.text() == controller_view_module.REMOVE_ARMED
    view.recheck()
    assert view.remove_button.text() == controller_view_module.REMOVE_IDLE

    view.remove_containers()
    assert calls == [], "the press after a cancel removed something"


def test_starting_or_stopping_also_disarms(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Any other server action means the user moved on."""
    for action in ("start_server", "stop_server"):
        view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
        calls = _watch_remove(view)
        view.remove_containers()
        getattr(view, action)()
        assert view.remove_button.text() == controller_view_module.REMOVE_IDLE, action
        view.remove_containers()
        assert calls == [], action


def test_a_removal_that_found_nothing_says_so(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """False means "there was nothing of ours", which is not the same as done."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _watch_remove(view, result=False)
    view.remove_containers()
    view.remove_containers()
    assert "no containers to remove" in view.problem_label.text()


UNIMPORTED = docker.ImportState(
    "absent", "none of acore_auth, acore_characters, acore_world exists on this server yet"
)
"""The one state the repair is offered for, and it is narrower than it was.

`partial` used to be here too. The live gate of 2026-08-23 took it away: an
import killed part-way leaves a schema that exists, and re-running the one-shot
over one of those does not finish it — AzerothCore skips the base data for a
database that is already there and records every remaining file as applied. So
a `partial` install is offered nothing, because there is nothing honest to
offer it. See `docker.ImportState.repairable`.
"""


def _watch_repair(
    view: ControllerView,
    state: docker.ImportState = UNIMPORTED,
    result: BaseException | bool = True,
    says: Sequence[str] = (),
) -> list[docker.OutputSink | None]:
    """Replace the controller's probe and repair with recorders.

    The view's job is the offering and the arming; whether the import is safe to
    run is `docker.repair_import()`'s, and that has its own tests including the
    refusal over a populated database.

    What each call was handed as its output sink is recorded rather than
    discarded: that argument is the whole of the progress feature, and it is
    also where the threading rule lives, so a test has to be able to look at it.
    `says` is what the fake import prints through it before finishing.
    """
    calls: list[docker.OutputSink | None] = []

    def fake_repair(output: docker.OutputSink | None = None) -> bool:
        calls.append(output)
        for line in says:
            if output is not None:
                output(line)
        if isinstance(result, BaseException):
            raise result
        return result

    view.services.controller.import_state = lambda: state  # type: ignore[method-assign]
    view.services.controller.repair_import = fake_repair  # type: ignore[method-assign]
    return calls


def _db_up(view: ControllerView, ps: _Ps) -> None:
    """The database running is what lets the tab ask about the import at all."""
    ps.names = "ac-database\n"
    view.refresh_status()


def test_the_repair_is_not_offered_until_the_database_says_it_is_needed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A destructive action that is always on screen is one that gets pressed by accident.

    The installer imports on every healthy path, so an offer to import again is
    only ever right for an install that is already broken — and the only thing
    that can say it is broken is the database itself.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.repair_button.isHidden(), "offered before anything was asked"

    _watch_repair(view, docker.ImportState("imported", "acore_world has 1103 tables"))
    _db_up(view, ps)
    assert view.repair_button.isHidden(), "offered on a database that is already imported"

    _watch_repair(view, docker.ImportState("populated", "651 rows in acore_auth.account"))
    view.recheck()
    assert view.repair_button.isHidden(), "offered on a database with characters on it"

    _watch_repair(view, docker.ImportState("unreadable", "no such container"))
    view.recheck()
    assert view.repair_button.isHidden(), "offered on the strength of a question nobody answered"

    _watch_repair(view, UNIMPORTED)
    view.recheck()
    assert not view.repair_button.isHidden(), "an unfinished import was never offered a repair"
    assert "none of acore_auth" in view.repair_label.text()


def test_the_repair_takes_two_presses_and_says_what_is_overwritten(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The teardown's warning says what is kept; this one has to say what is lost.

    It is offered because the probe found no accounts and no characters. If that
    is wrong — the wrong install, a probe that read a stale database — the
    sentence has to give the user somewhere else to go, and Restore is the path
    that keeps characters.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    calls = _watch_repair(view)
    _db_up(view, ps)

    view.repair_import()
    assert calls == [], "the first press imported something"
    assert view.repair_button.text() == controller_view_module.REPAIR_ARMED
    said = view.problem_label.text()
    assert "OVERWRITTEN" in said, said
    assert "restore a backup" in said, "no way out was offered"
    assert "Refresh" in said, "no way to cancel was offered"

    view.repair_import()
    assert len(calls) == 1
    assert view.repair_button.text() == controller_view_module.REPAIR_IDLE, "still armed after"


def test_the_two_destructive_buttons_are_never_armed_together(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Both write their warning into the same label, so one has to disarm the other.

    Two loaded buttons under one paragraph is a second press that does whichever
    of them the user had forgotten about.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    removals = _watch_remove(view)
    repairs = _watch_repair(view)
    _db_up(view, ps)

    view.remove_containers()
    view.repair_import()
    assert view.remove_button.text() == controller_view_module.REMOVE_IDLE
    assert view.repair_button.text() == controller_view_module.REPAIR_ARMED

    view.remove_containers()
    assert repairs == [], "arming the teardown left the import armed and it ran"
    assert removals == [], "the teardown ran on what was its first press again"


def test_refresh_start_and_stop_all_cancel_an_armed_repair(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Arming then walking away must not leave the most destructive button loaded."""
    for action in ("recheck", "start_server", "stop_server"):
        view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
        calls = _watch_repair(view)
        _db_up(view, ps)
        view.repair_import()
        assert view.repair_button.text() == controller_view_module.REPAIR_ARMED, action
        getattr(view, action)()
        assert view.repair_button.text() == controller_view_module.REPAIR_IDLE, action
        view.repair_import()
        assert calls == [], f"the press after {action} imported something"


def test_the_import_shows_its_own_output_instead_of_one_frozen_sentence(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Ten to thirty minutes of an unchanging label is indistinguishable from a hang.

    The label is read back after each line the fake import prints, because
    "shows the output" and "shows the output while it is still running" are
    different claims and only the second one is worth anything here. The window
    is the last two lines: this label sits above the rest of the tab, and a
    half-hour of import output accumulating in it is the same unbounded growth
    in a different place.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    printed = ["applying acore_auth", "applying acore_characters", "applying acore_world"]
    shown: list[str] = []

    def fake_repair(output: docker.OutputSink | None = None) -> bool:
        assert output is not None, "the import was run with nowhere to say anything"
        for line in printed:
            output(line)
            shown.append(view.problem_label.text())
        return True

    view.services.controller.import_state = lambda: UNIMPORTED  # type: ignore[method-assign]
    view.services.controller.repair_import = fake_repair  # type: ignore[method-assign]
    _db_up(view, ps)
    view.repair_import()
    view.repair_import()

    assert len(shown) == 3
    assert all(controller_view_module.IMPORT_RUNNING in text for text in shown), shown
    assert printed[0] in shown[0], "the first line was not on screen until the import ended"
    assert printed[1] in shown[2] and printed[2] in shown[2], shown[2]
    assert printed[0] not in shown[2], "every line is kept, so the label grows all import long"


def test_the_import_talks_through_a_relay_because_it_talks_from_a_worker_thread(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`repair_import()` calls its sink on the thread it runs on, which is not this one.

    Handing down the view's own `@Slot(str)` would look identical here and be a
    plain Python call from the worker thread into a widget. `LineRelay` is the
    difference, and only the identity of what gets passed down can pin it —
    running inline, as these tests do, the wrong version behaves the same.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    calls = _watch_repair(view, says=("applying acore_world",))
    _db_up(view, ps)
    view.repair_import()
    view.repair_import()

    assert len(calls) == 1
    sink = calls[0]
    assert getattr(sink, "__self__", None) is view._import_relay, (
        "the import was handed something that is not the relay, so its lines "
        "would reach a widget on the worker thread"
    )


def test_neither_the_armed_copy_nor_the_running_one_offers_a_stop(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """There is no cancel, so nothing may look like one.

    Abandoning a `compose up` means terminating it, which stops `ac-db-import`
    part-way through writing schemas. The tab therefore says so and disables
    every button while the import runs, rather than offering a Stop that would
    have to lie about what it does.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    disabled: list[bool] = []

    def fake_repair(output: docker.OutputSink | None = None) -> bool:
        buttons = (view.start_button, view.stop_button, view.remove_button, view.repair_button)
        disabled.append(not any(b.isEnabled() for b in buttons))
        return True

    view.services.controller.import_state = lambda: UNIMPORTED  # type: ignore[method-assign]
    view.services.controller.repair_import = fake_repair  # type: ignore[method-assign]
    _db_up(view, ps)
    view.repair_import()
    armed = view.problem_label.text()
    assert "cannot be stopped" in armed, armed
    view.repair_import()
    assert disabled == [True], "a button was live while the import it cannot stop was running"


def test_a_finished_repair_stops_offering_itself(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The remembered answer is stale the moment the import succeeds."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _watch_repair(view)
    _db_up(view, ps)
    assert not view.repair_button.isHidden()

    view.services.controller.import_state = lambda: docker.ImportState(  # type: ignore[method-assign]
        "imported", "acore_world has 1103 tables"
    )
    view.repair_import()
    view.repair_import()
    assert "import finished" in view.problem_label.text()
    assert view.repair_button.isHidden(), "still offering to import an install it just imported"


def test_a_refused_repair_is_readable_on_screen(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """`repair_import()` asks the database again itself and refuses on what it finds.

    That refusal names the accounts it found and points at Restore; discarded,
    the tab would say nothing at all about why the button did nothing.
    """
    refusal = docker.DockerCommandError(
        "this install's databases hold player data (651 rows in acore_auth.account)."
    )
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _watch_repair(view, result=refusal)
    _db_up(view, ps)

    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.repair_import()
    view.repair_import()
    assert "651 rows in acore_auth.account" in view.problem_label.text()
    assert failures and "player data" in failures[0]


def test_the_database_is_asked_about_its_import_once_per_time_it_comes_up(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The probe is three `docker exec`s and the status poll runs every five seconds.

    Asking on every poll would put that on a loop forever; asking once and never
    again would leave the answer wrong after the user fixed something.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    asked: list[int] = []

    def probe() -> docker.ImportState:
        asked.append(1)
        return UNIMPORTED

    view.services.controller.import_state = probe  # type: ignore[method-assign]

    _db_up(view, ps)
    view.refresh_status()
    view.refresh_status()
    assert asked == [1], "the probe ran on every poll"

    ps.names = ""  # the database went down again
    view.refresh_status()
    ps.names = "ac-database\n"
    view.refresh_status()
    assert asked == [1, 1], "the probe never ran again after the database came back"


# ------------------------------------------- what the review of 2026-08-23 found


def test_the_status_line_holds_still_while_an_action_of_ours_is_running(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A five-minute stop made a five-second poll into a liar.

    `stop_server()` writes "stopping…"; the poll then answered "db up, auth up,
    world up" and kept answering it for the whole drain, with both buttons dead
    and nothing on screen explaining why. Invisible at a ten-second stop, and
    not at a ninety-second one.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    ps.names = "\n".join([WOTLK.container_spec().db, WOTLK.container_spec().world])

    view._set_busy(True)
    view.status_label.setText("status: stopping…")
    view.refresh_status()
    assert view.status_label.text() == "status: stopping…", "the poll overwrote a live action"

    view._set_busy(False)
    view.refresh_status()
    assert "db up" in view.status_label.text(), "the label never came back"


def test_every_action_button_is_locked_while_an_action_runs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Remove and Repair stayed live during their own multi-minute actions.

    Two presses is not a defence when the button is still there afterwards: the
    second arm-and-press launches a second teardown or a second import, and
    whichever finishes first calls `_set_busy(False)` and unlocks Start while
    the other is still writing.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    view._set_busy(True)
    for name in ("start_button", "stop_button", "remove_button", "repair_button"):
        assert not getattr(view, name).isEnabled(), name
    view._set_busy(False)
    for name in ("remove_button", "repair_button"):
        assert getattr(view, name).isEnabled(), name


def test_closing_the_window_waits_out_the_grace_rather_than_aborting(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A ten-second join plus a five-minute stop is a documented process abort.

    `_JobWorker.run()` calls its work synchronously, so `thread.quit()` cannot
    interrupt a blocking `subprocess.run`, and `main.py` records that a QThread
    destroyed while running aborts the process (0xC0000409). The join therefore
    has to follow the grace rather than sit at a number chosen when a stop took
    ten seconds.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    waited: list[int] = []
    view._jobs.wait = lambda ms: waited.append(ms)  # type: ignore[attr-defined]
    view.shutdown()
    assert waited, "no join at all"
    assert waited[0] >= docker.STOP_GRACE_SECONDS * 1000, waited


def test_a_game_that_names_no_import_service_is_offered_no_repair(
    qapp: object, tmp_path: Path
) -> None:
    """`for_wotlk()` is called for every install, not only AzerothCore ones.

    `repair.import_state()` looks for the `acore_*` schemas by name, so wiring
    it unconditionally told a healthy CMaNGOS install its databases were never
    imported — and offered it the button that overwrites them.
    """
    spec = WOTLK.container_spec()
    assert spec.import_service, "wotlk should name one, or this test proves nothing"
    assert (
        ControllerServices.for_wotlk(WOTLK, tmp_path).controller.import_probe is not None
    ), "the game that HAS an import service lost its probe"

    without = WOTLK.model_copy(
        update={"containers": WOTLK.containers.model_copy(update={"db_import": None})}
    )
    assert ControllerServices.for_wotlk(without, tmp_path).controller.import_probe is None


# --------------------------------------- what the second review found (2026-08-23)


def _run_a_fake_import(view: ControllerView, during: object = None) -> list[object]:
    """Arm and fire Repair with the controller's repair replaced by a recorder.

    `during`, when given, is called while the import is notionally in flight —
    the point at which the tab is at its most misleading, and the only moment
    the findings below are reachable.
    """
    seen: list[object] = []

    def fake_repair(sink: object = None) -> bool:
        seen.append(sink)
        if callable(during):
            during()
        return True

    view.services.controller.repair_import = fake_repair  # type: ignore[method-assign]
    view.repair_import()
    view.repair_import()
    return seen


def test_refresh_is_locked_while_the_import_runs(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Refresh blanks the live output and probes the database being written into.

    `recheck()` sets `problem_label` to "", which during an import is the log the
    user is watching, and then fires `Controller.import_state()` - three
    `docker exec ... mysql` calls against the database `ac-db-import` is filling.
    The armed paragraph also teaches "press Refresh now", so it is exactly the
    button a hesitating user reaches for.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    enabled: list[bool] = []
    _run_a_fake_import(view, during=lambda: enabled.append(view.refresh_button.isEnabled()))
    assert enabled == [False], "Refresh was live during the import"
    assert view.refresh_button.isEnabled(), "Refresh never came back"


def test_the_stale_repair_offer_is_hidden_while_the_import_runs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """It sat under the live heading still saying the import had never finished."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    view.repair_label.setText("This install's databases were never finished")
    view.repair_label.setVisible(True)
    # `isHidden()`, not `isVisible()`: nothing is visible in an offscreen test
    # because no ancestor is shown, so `isVisible()` answers False either way
    # and the assertion would pass with the fix removed.
    assert not view.repair_label.isHidden(), "the offer was not up to begin with"
    hidden: list[bool] = []
    _run_a_fake_import(view, during=lambda: hidden.append(view.repair_label.isHidden()))
    assert hidden == [True], "the offer contradicted the heading above it"


def test_the_window_will_not_close_while_the_import_runs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Closing during one froze the window for 330s and then aborted the process.

    `shutdown()` joins the worker, `_JobWorker.run()` calls its work
    synchronously so `quit()` cannot preempt it, and a QThread destroyed while
    running aborts rather than warns. An import runs for 10-30 minutes, so the
    join always expired.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.busy_reason() is None, "a quiet tab refused to close"

    reasons: list[str | None] = []
    _run_a_fake_import(view, during=lambda: reasons.append(view.busy_reason()))
    assert reasons and reasons[0], "the close guard had nothing to say mid-import"
    assert "cannot be stopped" in reasons[0]
    assert view.busy_reason() is None, "the tab stayed unclosable after the import"


def test_the_armed_paragraph_does_not_offer_a_cancel_it_cannot_honour(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """It said "cannot be stopped once it has started" and "Press Refresh to cancel".

    Both were true of different moments and the paragraph did not say which, on
    the one screen where a user decides whether to overwrite their databases.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    view.services.controller.repair_import = lambda sink=None: True  # type: ignore[method-assign]
    view.repair_import()
    said = view.problem_label.text()
    assert "Press Refresh to cancel." not in said
    assert "while nothing has happened yet" in said, said
    assert "cannot be stopped" in said


def test_the_restore_warning_names_the_plan_and_does_not_overstate(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The one screen where somebody decides whether to overwrite their server.

    It used to append "Every character on the server is replaced" to EVERY
    allowed plan, with no check that `acore_characters` was in it — so a
    world-only restore threatened characters it would not touch. And "replaced"
    was wrong in the other direction: mysqldump emits `DROP TABLE IF EXISTS` per
    table and no `DROP DATABASE`, so a restore merges. Measured on Windows,
    2026-08-23: a table created after the backup survived a full restore of that
    schema.

    A warning that overstates on one axis and understates on the other is one a
    user learns to discount, which is the opposite of what it is for.
    """
    made = _FakeMaintenance()
    view = ControllerView(WOTLK, _services(ps, tmp_path, [], made), status_poll_ms=0)
    _add_backup(view, tmp_path)
    view.show_restore_plan()

    said = view.maintenance_report.toPlainText()
    assert "acore_characters" in said, said
    assert "Every character on the server is replaced" not in said
    assert "LEFT AS THEY ARE" in said, "the merge is not stated"
    assert "merges" in said
