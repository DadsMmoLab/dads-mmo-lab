"""Tests for `ControllerView` (roadmap 4.3) through `ControllerServices` fakes, offscreen."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from tests.conftest import HANG_BOUND, HANG_BOUND_MS, process_events, pump_until
from yulon import apply as apply_module
from yulon import (
    botlist,
    channel,
    channel_setup,
    commands,
    dashboard,
    docker,
    logsnap,
    manifest_store,
    networking,
    party,
    purge,
    runner,
    state,
    steam,
    tuning,
    useraccounts,
)
from yulon.apply import Applier, ApplyReport, DockerSql, required_prompts
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, Operations, load_catalog
from yulon.catalog.families import sqlplan
from yulon.catalog.installer import InstallerError
from yulon.controller import Controller
from yulon.controller_wow_tbc import controller as tbc_controller
from yulon.controller_wow_tortoise import accounts as tortoise_accounts
from yulon.controller_wow_tortoise import console as tortoise_console
from yulon.controller_wow_tortoise import controller as tortoise_controller
from yulon.controller_wow_vanilla import controller as vanilla_controller
from yulon.controller_wow_wotlk import console, modules
from yulon.controller_wow_wotlk.accounts import AccountError, AccountResult
from yulon.controller_wow_wotlk.console import ConsoleReply
from yulon.controller_wow_wotlk.maintenance import (
    BackupReport,
    DockerMysql,
    InterruptedRestore,
    MaintenanceError,
    RestorePlan,
    RestoreReport,
)
from yulon.git import RunnerGit
from yulon.manifest import Build, ConfKey, Manifest, ManifestType, Source, parse_manifest
from yulon.manifest_store import ManifestStore
from yulon.networking import NetworkPlan, NetworkReport
from yulon.runner import run as _REAL_RUN
from yulon.ui import controller_view as controller_view_module
from yulon.ui import lines as log_lines
from yulon.ui.controller_view import (
    RETURN_TO_PIN_BUTTON_LABEL,
    TUNING_RECREATE_LABEL,
    TUNING_RESTART_LABEL,
    UPDATE_TO_LATEST_BUTTON_LABEL,
    ControllerServices,
    ControllerView,
    DatabaseAlone,
    UpdateChoice,
    ask_update_choice,
)
from yulon.ui.widgets import modules_panel, tuning_panel
from yulon.ui.widgets.job import ThreadedJobRunner, run_inline
from yulon.ui.widgets.modules_panel import (
    BADGE_INSTALLED,
    BADGE_NOT_INSTALLED,
    NOT_IN_CATALOG,
)

WOTLK = load_catalog().get("wow-wotlk")
TBC = load_catalog().get("wow-tbc")
"""8.5b's tree: one bot signal, the account prefix, and no registry table."""
TORTOISE = load_catalog().get("wow-tortoise")
"""T36's client-folder tests: `required_file=None`, so a bare `Data/` warns rather than refuses."""


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
    """Records what the tab asked for instead of cloning — but NOT `update()`.

    `update()` is deliberately NOT overridden (round 2). It is the method that
    decides whether a `git reset --hard` may go near a folder, and a fake that
    answered for it would let the Modules tab's Update press be tested against
    a refusal path that never runs — which is exactly what the first version of
    `test_the_update_press…` did. So the real `Applier.update()` executes here,
    over a real `server_dir`, and only the `install()` it delegates to is
    faked.
    """

    def __init__(
        self,
        server_dir: Path | None = None,
        *,
        unmodified: bool | None = True,
        no_local_commits: bool | None = True,
        origin: str | None = None,
    ) -> None:
        super().__init__(
            server_dir or Path("/srv"),
            git=None,  # type: ignore[arg-type]
            unmodified=lambda _dest, _path: unmodified,
            no_local_commits=lambda _dest, _branch: no_local_commits,
            remote_url=lambda _dest: origin,
        )
        self.installed: list[str] = []
        # What the tab handed down as the `values` argument, per call. Recorded
        # because for two of the 41 shipped manifests that argument WAS the
        # defect: the tab called the applier without one at all, and the two
        # modules whose prompts have no default could only fail (2026-09-07).
        self.values: list[object] = []
        self.removed: list[str] = []

    def install(  # type: ignore[override]
        self, manifest: object, values: object = None, **kw: object
    ) -> ApplyReport:
        item_id = str(manifest.id)  # type: ignore[attr-defined]
        self.installed.append(item_id)
        self.values.append(values)
        return ApplyReport(
            "install",
            item_id,
            family=manifest.type,  # type: ignore[attr-defined]
            done=("clone",),
            rebuild_required=True,
        )

    def remove(self, manifest: object, values: object = None) -> ApplyReport:  # type: ignore[override]
        item_id = str(manifest.id)  # type: ignore[attr-defined]
        self.removed.append(item_id)
        self.values.append(values)
        return ApplyReport(
            "remove",
            item_id,
            family=manifest.type,  # type: ignore[attr-defined]
            done=("rm -r",),
        )


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


class _StubRecorder:
    """A `logsnap.Recorder` that answers with a fixed snapshot, without a daemon."""

    def __init__(self, snapshot: logsnap.Snapshot) -> None:
        self.last = snapshot

    def __call__(self) -> logsnap.Snapshot:
        return self.last


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


def test_the_sub_tabs_carry_icon_and_title_in_the_tab_itself(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The sub-tabs carry both their icon and their readable title label directly."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)

    for i in range(view._tabs.count()):
        assert view._tabs.tabText(i) != "", f"tab {i} missing its title"
        assert view._tabs.tabToolTip(i) == view._tabs.tabText(i)

    assert view._tabs.tabText(0) == "Server"
    assert view._tabs.tabText(2) == "Accounts"


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
    assert "Only one server can run at a time" in view.problem_label.text()
    assert "3724" in view.problem_label.text(), "the message does not say which port"
    assert "tbc-realmd" in failures[0]
    assert not any(c[:4] == ["docker", "compose", "up", "-d"] for c in ps.calls)

    # The offer, not just the refusal: naming the blocker and leaving the user to
    # go and stop it themselves was the old dead end. The button appears with the
    # collision and goes away with it.
    # isHidden(), not isVisible(): nothing shows this window in a test, so every
    # widget in it is invisible either way. isHidden() answers the question that
    # was actually asked - did the code hide it or not.
    assert not view.stop_other_button.isHidden(), "the offer to stop never appeared"

    ps.ports = ""
    view.start_server()
    assert any(c[:5] == ["docker", "compose", "up", "-d", "--no-deps"] for c in ps.calls)
    assert view.problem_label.text() == ""
    assert view.stop_other_button.isHidden(), "the offer outlived the collision"
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


def test_a_wsl_console_is_usable_on_a_host_that_has_no_pty_of_its_own(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows + a server inside WSL: no pty here, and Send still works.

    The tab used to ask `pty_supported()`, which is a fact about THIS process
    and not about whether the console can be reached. The distro can open a
    terminal even where Windows cannot (measured 2026-08-27), so the question
    the button asks had to change with the transport - otherwise the fix ships
    behind a button that is still greyed out.
    """
    monkeypatch.setattr(console, "pty_supported", lambda: False)
    services = _services(ps, tmp_path, [])
    services.controller = Controller(WOTLK.container_spec(), tmp_path, wsl_distro="dml-arch")
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert view.send_button.isEnabled()
    assert view.command_edit.isEnabled()
    assert view.console_note.text() == "", view.console_note.text()


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
    # The asker is injected because `mod-ah-bot` is one of the two manifests
    # that now HAS a question: with the real one this test would sit on a modal
    # dialog forever, which is exactly what it did when the seam was added and
    # this line was not (2026-09-07).
    view = ControllerView(
        WOTLK,
        _services(ps, tmp_path, []),
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts: {p.key: "42" for p in prompts},
    )
    assert len(view.modules_panel.rows()) >= 40
    view.modules_panel.select("mod-ah-bot")
    assert view.selected_manifest() is not None and view.selected_manifest().id == "mod-ah-bot"
    view._module_action("install")
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier) and applier.installed == ["mod-ah-bot"]
    # It used to say "worldserver REBUILD required before this takes effect",
    # which sent the reader hunting for a button no tab has (FACT 4, 2026-09-07).
    assert "C++ module" in view.module_report.toPlainText()


def test_the_report_says_which_kind_of_module_this_is() -> None:
    """21 of the 41 shipped manifests need no recompile; 20 do. Different sentences.

    Counted 2026-09-07 through `parse_manifest`: `build.rebuild` is true for 20
    manifests, all of type `module`, and false for the other 21 (7 ale, 2 keg,
    11 mod, and `mod-arac`). "No module works until someone rebuilds" would be
    false for half of them, which is why the kind is on the report rather than
    in the sentence.
    """
    cpp = controller_view_module._format_report(
        ApplyReport("install", "mod-solocraft", family="module", rebuild_required=True)
    )
    data_only = controller_view_module._format_report(
        ApplyReport("install", "sitmeanrest", family="module", restart_recommended=True)
    )
    assert "C++ module" in cpp and "C++ module" not in data_only
    assert "Stop" in data_only and "Server tab" in data_only


def test_removing_a_cpp_module_is_not_told_it_is_inert_on_disk() -> None:
    """The same flag, the opposite situation: the binary may still HAVE it.

    "may": the live run on yulon-ubuntu 2026-09-07 removed two modules that had
    been installed minutes earlier and never built, and a draft that said "its
    code was compiled into the worldserver" asserted of both something that was
    false of both. The app cannot know what went into the last build — that is
    the same ignorance `REBUILD_HISTORY` records — so it says which case
    would be bad rather than claiming to know which case this is.
    """
    text = controller_view_module._format_report(
        ApplyReport(
            "remove", "mod-solocraft", family="module", done=("rm -r",), rebuild_required=True
        )
    )
    assert "If mod-solocraft was in the last build it is still in there" in text
    assert "is on disk and inert" not in text


def test_pending_sql_is_drawn_as_not_applied_with_the_file_count() -> None:
    """The line the user reads in place of the tick that was a lie.

    The real report from the live applier, yulon-ubuntu 2026-09-07, read
    `DONE: sql data/sql/db-world/*.sql -> world: left to ac-db-import on next
    start` — with nothing run and nothing checked.
    """
    text = controller_view_module._format_report(
        ApplyReport(
            "install",
            "mod-aoe-loot",
            family="module",
            rebuild_required=True,
            pending_sql=(
                apply_module.PendingSql(
                    db="world",
                    path="data/sql/db-world/*.sql",
                    files=("data/sql/db-world/aoe_loot_module_string.sql",),
                ),
            ),
        )
    )
    assert "NOT applied" in text
    assert "1 file" in text
    assert "left to ac-db-import" not in text
    assert "has not been applied" in text
    assert controller_view_module.MODULE_SQL_BUTTON_LABEL in text, (
        "the line tells the user to press something whose name is not on the tab: " f"{text!r}"
    )


def test_a_glob_that_matched_nothing_is_not_drawn_as_a_module_with_no_sql() -> None:
    """Measured live, yulon-ubuntu 2026-09-07, on the first run of this code.

    The real applier installed `mod-aoe-loot` into `/home/user/wowserver` and its
    manifest glob `data/sql/db-world/*.sql` resolved to nothing — while that
    clone carries `data/sql/db-world/base/aoe_loot_module_string.sql`, the file
    FACT 1 had watched the importer apply an hour earlier. The draft said
    "nothing to apply". Two sibling modules cloned the same minute keep theirs
    directly in `db-world/` (mod-solocraft 1 file, mod-transmog 3), so the
    layout is per-repository and a zero match is this app failing to count.
    """
    text = controller_view_module._format_report(
        ApplyReport(
            "install",
            "mod-aoe-loot",
            family="module",
            rebuild_required=True,
            pending_sql=(
                apply_module.PendingSql(db="world", path="data/sql/db-world/*.sql", files=()),
            ),
        )
    )
    assert "nothing to apply" not in text
    assert "NOT applied" in text
    assert "has not been applied" in text
    assert controller_view_module.MODULE_SQL_BUTTON_LABEL in text, (
        "the line tells the user to press something whose name is not on the tab: " f"{text!r}"
    )


def _select_module(view: ControllerView, item_id: str) -> None:
    assert item_id in _panel_ids(view), f"{item_id} is in no row of the Modules tab"
    view.modules_panel.select(item_id)


def test_installing_a_module_whose_prompt_has_no_default_asks_first(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """FACT 3, 2026-09-07: the tab called the applier with no `values` at all.

    `mod-ah-bot` and `mod-ah-bot-plus` are the only two shipped manifests with a
    prompt carrying no default, so pressing Install on either of them could only
    ever produce `conf AuctionHouseBot.GUIDs: no value for {bot_guid}` — and
    they are exactly the two the owner could test.
    """
    asked: list[tuple[str, tuple[str, ...]]] = []

    def asker(parent: object, manifest: object, prompts: object) -> dict[str, str]:
        asked.append(
            (str(manifest.id), tuple(p.key for p in prompts))  # type: ignore[attr-defined]
        )
        return {"bot_guid": "42", "bot_account": "7"}

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0, prompt_asker=asker)
    _select_module(view, "mod-ah-bot")
    view._module_action("install")

    assert asked == [("mod-ah-bot", ("bot_guid", "bot_account"))]
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier)
    assert applier.installed == ["mod-ah-bot"]
    assert applier.values == [{"bot_guid": "42", "bot_account": "7"}]


def test_cancelling_the_questions_installs_nothing(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = ControllerView(
        WOTLK,
        _services(ps, tmp_path, []),
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts: None,
    )
    _select_module(view, "mod-ah-bot-plus")
    view._module_action("install")

    applier = view.services.applier
    assert isinstance(applier, _FakeApplier)
    assert applier.installed == [] and applier.values == []
    assert "cancelled" in view.module_report.toPlainText().lower()


def test_exactly_the_manifests_with_a_question_are_asked(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Which manifests open a dialog on Install, pinned by NAME (T92 widened it).

    Until 2026-09-22 this pinned `["mod-ah-bot", "mod-ah-bot-plus"]` -- the two
    with a prompt carrying no default -- and every other manifest's prompts
    were never shown: the defaults were written unseen. Now every manifest
    whose install renders a prompt asks, pre-filled; the ones with no prompt
    at all still get no window. A manifest gaining or losing a question shows
    up here by name.

    Rows whose Install is LOCKED are left out of the loop rather than counted
    as installs (T69): eleven shipped manifests declare a `requires`, nothing
    is on disk in this fixture, so `_module_action()` refuses them before the
    asker — which is the guard's whole job and is asserted by its own test.
    """
    asked: list[str] = []

    def asker(parent: object, manifest: object, prompts: object) -> dict[str, str]:
        asked.append(str(manifest.id))  # type: ignore[attr-defined]
        assert prompts, "a dialog with no question is a window for nothing"
        return {p.key: "1" for p in prompts}  # type: ignore[attr-defined]

    services = _services(ps, tmp_path, [])
    # T62: five of these manifests also write into the game client, and with no
    # client folder on the install each of them now stops at the client notice
    # before any question is asked. This test is about the PROMPTS, so the
    # install is given a folder and every row reaches the applier as before.
    services.client_dir = tmp_path / "client"
    view = ControllerView(WOTLK, services, status_poll_ms=0, prompt_asker=asker)
    catalogued = [
        r.data.id for r in view.modules_panel.rows() if r.data.catalogued and r.data.installable
    ]
    locked = [
        r.data.id for r in view.modules_panel.rows() if r.data.catalogued and not r.data.installable
    ]
    assert "mod-ah-bot" in catalogued and "mod-ah-bot-plus" in catalogued, locked
    for item_id in catalogued:
        view.modules_panel.select(item_id)
        view._module_action("install")

    store = modules.store()
    expected = sorted(
        m.id
        for kind in ("module", "ale", "keg", "mod")
        for m in store.load_all(kind)  # type: ignore[arg-type]
        if m.id in catalogued and required_prompts(m, "install")
    )
    # `sitmeanrest` is absent: it requires mod-ale, locked in this fixture (T69).
    assert "mod-ah-bot" in expected and "xp-rates" in expected and "nerf-mobs" in expected
    assert sorted(asked) == expected, asked
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier)
    assert len(applier.installed) == len(catalogued)
    unasked = [v for m, v in zip(applier.installed, applier.values, strict=True) if m not in asked]
    assert all(v is None for v in unasked), "a manifest with no question was given values"


def test_the_menu_install_of_a_blocked_row_refuses_before_it_asks_anything(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The context menu reaches `_module_action()` even when the row's button is locked (T55).

    With the shipped catalog: `mod-ah-bot` installed, `mod-ah-bot-plus` pressed.
    The prompt dialog runs before the applier, and `mod-ah-bot-plus` asks a
    question with no default -- so without the guard the user answered it and
    was then refused by `_conflict_refusal()`.

    Mutation: delete the guard in `_module_action()` and the asker is called and
    the fake applier is handed the install.
    """
    asked: list[str] = []
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "installed_modules", lambda: {"module": frozenset({"mod-ah-bot"})})
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts: asked.append(manifest.id) or {},
    )
    _select_module(view, "mod-ah-bot-plus")
    row = view.modules_panel.row("mod-ah-bot-plus")
    assert row.install_button is not None and not row.install_button.isEnabled()

    view._module_action("install")

    assert asked == []
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier) and applier.installed == []
    report = view.module_report.toPlainText()
    assert "not started" in report and "Auction House Bot" in report, report
    assert "Nothing on this machine was changed" in report


def test_a_failed_press_redraws_the_conflict_lock_from_the_disk(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A failure can change what is installed, so the locks are re-read on it too (T55 review).

    `Applier.install()` clones before deploy, SQL and conf, and any of those can
    raise with the clone already on disk; `remove()` deletes deployed files
    before the rmtree that may fail. The success path re-read the disk; the
    failure path did not, so the row kept the pre-press lock.

    Driven with the installed-modules seam changing under the failure, both ways.

    Mutation: drop `reload_modules()` from `_module_failed()` and the first
    assertion after each failure keeps the old answer.
    """
    on_disk: dict[str, frozenset[str]] = {"module": frozenset()}
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "installed_modules", lambda: dict(on_disk))
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert view.modules_panel.row("mod-ah-bot-plus").data.installable

    # An install of mod-ah-bot that cloned and then raised.
    on_disk["module"] = frozenset({"mod-ah-bot"})
    view._acting_on = view._manifests[("module", "mod-ah-bot")]
    view._module_pending = "install mod-ah-bot"
    view._module_failed(RuntimeError("the SQL step raised after the clone"))
    assert not view.modules_panel.row("mod-ah-bot-plus").data.installable
    assert "install mod-ah-bot FAILED" in view.module_report.toPlainText()

    # A remove of it that took the clone away and then raised.
    on_disk["module"] = frozenset()
    view._acting_on = view._manifests[("module", "mod-ah-bot")]
    view._module_pending = "remove mod-ah-bot"
    view._module_failed(RuntimeError("a later step raised after the rmtree"))
    assert view.modules_panel.row("mod-ah-bot-plus").data.installable
    assert "remove mod-ah-bot FAILED" in view.module_report.toPlainText()


def test_installing_a_manifest_with_defaults_still_asks(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A prompt WITH a default opens the dialog too, pre-filled, and the answers reach the applier.

    Until T92 the gate opened only for a prompt with no default, which in the
    shipped catalog is `mod-ah-bot`'s GUIDs and nothing else: `xp-rates` never
    asked its rates and `sitmeanrest` never asked its seconds.
    """
    asked: list[str] = []
    services = _services(ps, tmp_path, [])
    # sitmeanrest requires mod-ale; say it is there so Install is not locked (T69).
    object.__setattr__(services, "installed_modules", lambda: {"module": frozenset({"mod-ale"})})
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts: asked.append(manifest.id)
        or {p.key: "7" for p in prompts},
    )
    _select_module(view, "sitmeanrest")
    view._module_action("install")

    assert asked == ["sitmeanrest"]
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier) and applier.installed == ["sitmeanrest"]
    assert applier.values[-1] == {
        key: "7"
        for key in (
            "duration",
            "regen_aura",
            "rest_xp_enabled",
            "rest_xp_delay",
            "rest_xp_rate",
            "rest_xp_max_levels",
        )
    }


def test_removing_the_ah_bot_asks_nothing(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Remove renders no template on either manifest, so it must not interrogate."""
    asked: list[str] = []
    view = ControllerView(
        WOTLK,
        _services(ps, tmp_path, []),
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts: asked.append(manifest.id) or {},
    )
    _select_module(view, "mod-ah-bot")
    view._module_action("remove")

    assert asked == []
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier) and applier.removed == ["mod-ah-bot"]


# ------------------------------- the module SQL that no other button reaches
#
# `docker.apply_module_sql()` and its per-game binding were measured working on
# yulon-ubuntu (2026-09-07) and had NO caller in `yulon/ui/` at all — a route
# with no button on it, which is the same thing as no route for anyone who is
# not reading the source. These tests are that button.


class _FakeImporter:
    """Stands in for `controller_wow_wotlk.modules.apply_module_sql`.

    Records what the view handed down and what the tab looked like WHILE the
    run was in flight — the second one is the only way to see a lock that a
    synchronous job runner puts back before the next line of the test.
    """

    def __init__(
        self, says: Sequence[str] = (), refusal: Exception | None = None, returncode: int = 0
    ) -> None:
        self.says = tuple(says)
        self.refusal = refusal
        self.returncode = returncode
        self.sinks: list[object] = []
        self.enabled_in_flight: list[bool] = []
        self.view: ControllerView | None = None
        self.during: Callable[[], object] | None = None

    def __call__(self, output: object = None) -> docker.AttachedRun:
        self.sinks.append(output)
        if self.view is not None:
            self.enabled_in_flight.append(self.view.module_sql_button.isEnabled())
        if self.during is not None:
            self.during()
        for line in self.says:
            if callable(output):
                output(line)
        if self.refusal is not None:
            raise self.refusal
        return docker.AttachedRun(self.returncode, self.says)


def _importer_view(
    ps: _Ps, tmp_path: Path, importer: _FakeImporter | None
) -> tuple[ControllerView, _FakeImporter | None]:
    services = _services(ps, tmp_path, [])
    services.module_sql = importer
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    if importer is not None:
        importer.view = view
    return view, importer


def test_the_modules_tab_can_apply_the_module_sql_nothing_else_applies(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The whole box in one press: the route exists, and now something reaches it.

    The line asserted here is the importer's own evidence that a module's SQL
    was applied — `>> Applying update <file>.sql`, measured on yulon-ubuntu
    2026-09-07 while `acore_world.updates` went 2967 → 2968. The tab shows what
    the importer said rather than a sentence of its own, because the run's
    output is the only thing that knows whether anything was applied.
    """
    view, importer = _importer_view(
        ps, tmp_path, _FakeImporter(says=(">> Applying update aoe_loot_module_string.sql",))
    )
    assert importer is not None
    view.apply_module_sql()

    assert len(importer.sinks) == 1
    assert ">> Applying update aoe_loot_module_string.sql" in view.module_report.toPlainText()


def test_the_refusal_that_makes_this_not_a_button_that_always_works(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A running world refuses, and the refusal is what the user reads.

    Checklist 8.7a's rule lives in `docker.apply_module_sql()` — one guard,
    inside the step every caller passes through — so the tab must NOT spell a
    second copy of it. What the tab owes is that the refusal arrives on screen
    intact and that nothing is claimed to have been applied.
    """
    refusal = docker.DockerCommandError(
        "ac-worldserver is running. The importer writes to the databases underneath them, and "
        "a running worldserver holds characters in memory and saves them back over whatever it "
        "finds. Press Stop first, then try again."
    )
    view, importer = _importer_view(ps, tmp_path, _FakeImporter(refusal=refusal))
    assert importer is not None
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    view.apply_module_sql()

    report = view.module_report.toPlainText()
    assert "Press Stop first" in report
    assert "FAILED" in report
    assert "applied" not in report.lower(), report
    assert failures and "Press Stop first" in failures[0]


def test_the_importer_talks_through_a_relay_because_it_talks_from_a_worker_thread(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The sink handed down must be the relay's emitter, never a bound slot of the view.

    Same reason the repair's sink is: this call runs on a worker thread and
    everything it invokes runs there too, so a bound `@Slot(str)` would write
    into a widget from off the GUI thread. Running inline, as these tests do,
    the wrong version behaves identically — only the identity of what was
    passed down can tell them apart.
    """
    view, importer = _importer_view(ps, tmp_path, _FakeImporter(says=("one",)))
    assert importer is not None
    view.apply_module_sql()

    sink = importer.sinks[0]
    assert getattr(sink, "__self__", None) is view._module_sql_relay, (
        "the importer was handed something that is not the relay, so its lines "
        "would reach a widget on the worker thread"
    )


def test_the_module_sql_button_is_locked_while_the_importer_runs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The button is dead for the length of the run, and alive again after it.

    `run_one_shot(allowed_modules=...)` is `compose run --rm`, which starts a
    NEW container each time rather than refusing because one is already up, so
    nothing below this tab would stop a second press from racing the first —
    the disabled button is the whole of that defence.

    Two presses here are two runs, and that is the test being honest rather
    than the lock failing: these tests run their jobs inline, so the first has
    already finished by the second line. What is asserted is what the tab
    looked like WHILE each one ran.
    """
    view, importer = _importer_view(ps, tmp_path, _FakeImporter(says=("one",)))
    assert importer is not None
    view.apply_module_sql()
    view.apply_module_sql()

    assert importer.enabled_in_flight == [False, False], importer.enabled_in_flight
    assert view.module_sql_button.isEnabled(), "the button never came back"


def test_the_window_will_not_close_while_the_module_importer_runs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The same one-shot service, so the same close guard — which it did not have.

    `busy_reason()` covered the repair alone and said so ("Only the import"),
    because until this tab had a second button that runs `ac-db-import` that
    was true. `_JobWorker.run()` calls its work synchronously, `quit()` cannot
    preempt a blocking `subprocess`, and a QThread destroyed while running
    aborts the process (0xC0000409) — so a close during a module import that
    outlives the join is the recorded crash, not a slow exit.

    Shorter than a full import, and that is not a defence: how many pending
    files a module set has is not something this tab gets to assume.
    """
    reasons: list[str | None] = []
    importer = _FakeImporter(says=("one",))
    view, _ = _importer_view(ps, tmp_path, importer)
    assert view.busy_reason() is None, "a quiet tab refused to close"

    importer.during = lambda: reasons.append(view.busy_reason())
    view.apply_module_sql()

    assert reasons and reasons[0], "the close guard had nothing to say mid-run"
    assert "importer" in reasons[0]
    assert view.busy_reason() is None, "the tab stayed unclosable afterwards"


def test_a_game_with_no_importer_is_offered_no_module_sql_button(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`module_sql` is None for the three CMaNGOS games, and the tab says so rather than fails.

    The disabled button is the honest shape: pressing it would reach
    `docker.apply_module_sql()`'s first refusal ("this game does not say which
    compose service imports its databases"), which is a true sentence delivered
    after a click that could never have worked.
    """
    view, _ = _importer_view(ps, tmp_path, None)

    assert view.module_sql_button.isEnabled() is False
    assert "no" in view.module_sql_button.toolTip().lower()
    view.apply_module_sql()  # a press that gets through must not raise
    assert view.module_report.toPlainText() == ""


def test_only_the_game_that_names_an_importer_is_wired_a_module_sql_route(
    tmp_path: Path,
) -> None:
    """The wiring, not the tab: which games really get the route, asked of the factories.

    A defaulted `None` field is exactly the shape that can be forgotten — the
    tab would then be permanently disabled on the game that HAS an importer and
    no test of the view would notice, because the view was handed a fake.
    """
    assert ControllerServices.for_entry(WOTLK, tmp_path).module_sql is not None
    for game in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        entry = load_catalog().get(game)
        assert entry.container_spec().import_service == "", f"{game} now names an importer"
        assert ControllerServices.for_entry(entry, tmp_path).module_sql is None, game

    # And the same question asked of the WotLK factory itself, so that what the
    # route is conditional on is `import_service` and not the game's name.
    without = WOTLK.model_copy(
        update={"containers": WOTLK.containers.model_copy(update={"db_import": None})}
    )
    assert ControllerServices.for_entry(without, tmp_path).module_sql is None


def test_an_importer_that_is_running_locks_the_other_one(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Repair and Apply module SQL run the SAME one-shot service against the same databases.

    So the tab's busy lock has to cover both, in both directions, and it has to
    put back what this install really has rather than unconditionally: a
    CMaNGOS install whose Stop has just finished must not be handed a live
    Apply module SQL button it can only be refused for.
    """
    view, _ = _importer_view(ps, tmp_path, _FakeImporter())
    view._set_busy(True)
    assert not view.module_sql_button.isEnabled()
    view._set_busy(False)
    assert view.module_sql_button.isEnabled()

    without, _ = _importer_view(ps, tmp_path, None)
    without._set_busy(True)
    without._set_busy(False)
    assert not without.module_sql_button.isEnabled(), "a game with no importer got a live button"


def test_the_module_sql_route_reaches_this_games_own_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the wired lambda really calls: the per-game binding, with THIS install's folder.

    The seam under it (`docker.apply_module_sql`) takes a spec and a directory,
    so a call site that passed the wrong directory would run the importer
    against somebody else's install and look identical from the tab.
    """
    seen: dict[str, object] = {}

    def fake(server_dir: Path, **kwargs: object) -> docker.AttachedRun:
        seen["server_dir"] = server_dir
        seen.update(kwargs)
        return docker.AttachedRun(0, ())

    monkeypatch.setattr(modules, "apply_module_sql", fake)
    route = ControllerServices.for_entry(WOTLK, tmp_path).module_sql
    assert route is not None
    route(print)
    assert seen["server_dir"] == tmp_path
    assert seen["output"] is print


# ------------------------------- how far behind each installed module is (8.7a)


def test_the_modules_tab_shows_how_far_behind_each_installed_module_is(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.7a's first clause, at the control the user presses.

    The figure comes up from `apply.module_updates()` already formatted — the
    view never builds the sentence — because the one thing this clause is about
    is that the number on screen equals the same range run by hand.
    """
    rows = (
        apply_module.ModuleUpdate(
            key="mod-aoe-loot", path=tmp_path / "mod-aoe-loot", is_checkout=True, behind=3
        ),
        apply_module.ModuleUpdate(
            key="mod-playerbots", path=tmp_path / "mod-playerbots", is_checkout=True, behind=0
        ),
    )
    services = _services(ps, tmp_path, [])
    services.module_updates = lambda: rows
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert view.module_updates_button.isEnabled()

    view.check_module_updates()

    text = view.module_report.toPlainText()
    assert "mod-aoe-loot: 3 commits behind" in text
    assert "mod-playerbots: 0 commits behind" in text


# --------------------------------------------- a module from a link or a folder
#
# Lane C of `.notes/phase8-designs/module-from-link-or-folder.md`: the two
# buttons, the two dialog seams, and what the tab does with what they answer.
# Every service below is a fake, which is the whole point of the seam -- the
# real ones are lane A's (`module_source.py`, absent from this branch) and the
# install route behind them is lane B's.
#
# Prior art, `origin/rust-main`: the same control is
# `launcher/src/lib/pages/ModuleManager.svelte:1473-1491` -- an "Install from
# URL" card with a `mod-* repos only` hint and a button dead while the field is
# empty -- and its handler at `:447-455` clears the field on success, after the
# `await refresh()` at `:439` that re-reads the list.


def _custom_manifest(item_id: str, description: str) -> Manifest:
    """A manifest of the shape lane A's `derive_link`/`derive_folder` return."""
    return Manifest(
        id=item_id,
        name=item_id,
        type="module",
        game="wow-wotlk",
        description=description,
        source=Source(repo=f"https://github.com/you/{item_id}"),
        build=Build(rebuild=True),
    )


CUSTOM_LINK_DESC = "Custom module (cloned from a URL you provided)."
"""Verbatim `origin/rust-main:crates/dml-wow/src/modules.rs:38`."""

CUSTOM_FOLDER_DESC = "Custom module (copied from a folder you provided)."
"""No prior art: `origin/rust-main` had no folder route at all."""


class _LayeredStore(ManifestStore):
    """The bundled store with a user layer over it, which is lane A's §2.3 shape.

    The row a custom install adds to the list does NOT come from the view
    remembering it: it comes from the store, because lane A persists the
    derived manifest under `config_dir()/manifests/user/<game>/` and reads that
    layer back after the bundled index. Modelled here rather than asserted
    against a view-held dict, so a view that quietly kept its own copy would
    still fail these tests -- its row would survive a `reload_modules()` that
    the real store answers without it.

    User items follow bundled items, and a user id that is shipped never
    shadows the shipped one, both of which are lane A's rules.
    """

    def __init__(self, root: Path, game: str) -> None:
        super().__init__(root, game)
        self.user: dict[str, Manifest] = {}

    def load_all(
        self, kind: ManifestType, *, skipped: list[str] | None = None
    ) -> Iterator[Manifest]:
        # T46's keyword is forwarded rather than swallowed: the real store names
        # a user manifest it could not load through it, and a fake that dropped
        # it would make the view look like it reports skips when it never sees any.
        shipped = list(super().load_all(kind, skipped=skipped))
        yield from shipped
        ids = {m.id for m in shipped}
        for manifest in self.user.values():
            if manifest.type == kind and manifest.id not in ids:
                yield manifest


class _FakeCustomRoute:
    """Lane A's bindings and lane B's install, as one recording fake."""

    def __init__(self, store: _LayeredStore, refusal: str | None = None) -> None:
        self.store = store
        self.refusal = refusal
        self.derived_from: list[object] = []
        self.installed: list[tuple[str, Path | None]] = []
        self.replacing: list[bool] = []
        self.question: str | None = None
        self.forgotten: list[str] = []
        self.custom_ids: set[str] = set()

    def derive_link(self, text: str) -> Manifest:
        if self.refusal is not None:
            raise ValueError(self.refusal)
        self.derived_from.append(text)
        item_id = text.rstrip("/").rsplit("/", 1)[-1]
        self.custom_ids.add(item_id)
        return _custom_manifest(item_id, CUSTOM_LINK_DESC)

    def derive_folder(self, path: Path) -> Manifest:
        if self.refusal is not None:
            raise ValueError(self.refusal)
        self.derived_from.append(path)
        self.custom_ids.add(path.name)
        return _custom_manifest(path.name, CUSTOM_FOLDER_DESC)

    def install(
        self, manifest: Manifest, folder: Path | None, *, replacing: bool = False
    ) -> ApplyReport:
        # `replacing` is recorded, not ignored: it is the user's answer to T47's
        # question, and an install that dropped it would reset a checkout of
        # another repository with the question asked and the answer thrown away.
        self.installed.append((manifest.id, folder))
        self.replacing.append(replacing)
        # Lane A's `complete()` persists inside the install pass, so the row is
        # in the store by the time the report comes back.
        self.store.user[manifest.id] = manifest
        return ApplyReport(
            "install", manifest.id, family=manifest.type, done=("clone",), rebuild_required=True
        )

    def forget(self, manifest: Manifest) -> bool:
        self.forgotten.append(manifest.id)
        return self.store.user.pop(manifest.id, None) is not None


def _with_custom_route(
    services: ControllerServices, refusal: str | None = None
) -> _FakeCustomRoute:
    """Put a layered store and the five custom-module seams on `services`, plus T47's question."""
    store = _LayeredStore(modules.BUNDLED_MANIFESTS_DIR, modules.GAME)
    route = _FakeCustomRoute(store, refusal=refusal)
    services.store = store
    services.module_from_link = route.derive_link
    services.module_from_folder = route.derive_folder
    services.module_install_custom = route.install
    services.module_replacement_question = lambda _manifest: route.question
    services.module_forget = route.forget
    return route


def _listed(view: ControllerView) -> list[str]:
    """Every row's name and description, the way the cards read them out."""
    return [f"{r.data.name} — {r.data.description}" for r in view.modules_panel.rows()]


def _rows_for(view: ControllerView, item_id: str) -> list[str]:
    """Every row whose manifest id is `item_id` -- the id, not the visible text.

    A shipped manifest's row is titled `AoE Loot`: the id appears nowhere in the
    words, so a search over the text finds a custom module (whose name IS its
    id) and silently misses every shipped one.
    """
    return [r.data.id for r in view.modules_panel.rows() if r.data.id == item_id]


def test_the_link_button_derives_installs_and_relists_as_a_custom_module(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The whole link clause: derive, install through the seam, then re-list.

    The re-list is the half that is easy to drop and hard to notice from the
    report alone: without it the module is on disk, the report says so, and the
    list the user selects Remove from does not carry it until the next start.

    The ground is read first and asserted absent, because a list that already
    held the row would make the assertion below true before the press.
    """
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services)
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-thing",
    )
    assert not any("mod-my-thing" in line for line in _listed(view))

    view.install_module_from_link()

    assert route.derived_from == ["https://github.com/you/mod-my-thing"]
    assert route.installed == [("mod-my-thing", None)]
    assert "install mod-my-thing:" in view.module_report.toPlainText()
    assert f"mod-my-thing — {CUSTOM_LINK_DESC}" in _listed(view)
    view.modules_panel.select("mod-my-thing")
    chosen = view.selected_manifest()
    assert chosen is not None and chosen.id == "mod-my-thing"


def test_a_refused_link_says_the_sentence_and_installs_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A refusal is lane A's sentence, shown verbatim, with no job queued."""
    refusal = (
        "The repository is named 'tools', and a custom module must be named "
        "mod-<something> in lowercase letters, digits and hyphens. "
        "Nothing on this machine was changed."
    )
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services, refusal=refusal)
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/tools",
    )
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    view.install_module_from_link()

    assert view.module_report.toPlainText() == refusal
    assert failures == [refusal]
    assert route.installed == []


def test_cancelling_the_link_dialog_changes_nothing(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Cancel is not empty text: nothing is derived and nothing is installed."""
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services)
    view = ControllerView(WOTLK, services, status_poll_ms=0, link_asker=lambda parent, title: None)

    view.install_module_from_link()

    assert route.derived_from == [] and route.installed == []
    assert view.module_report.toPlainText() == (
        "install from link: cancelled — nothing on this machine was changed."
    )


def test_the_folder_button_hands_the_install_route_the_folder_it_was_given(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The folder is derived from and then carried into the install call.

    The design has the view build lane B's `FolderSource` and hand it the
    copier; lanes A and B are not on this branch, so the view hands the route
    the path alone and the route (lane A's binding) owns the copier. Recorded
    as deviation D1 in the gate README.
    """
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services)
    source = tmp_path / "mod-hand-made"
    source.mkdir()
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        folder_asker=lambda parent, title: source,
    )
    assert not any("mod-hand-made" in line for line in _listed(view))

    view.install_module_from_folder()

    assert route.derived_from == [source]
    assert route.installed == [("mod-hand-made", source)]
    assert f"mod-hand-made — {CUSTOM_FOLDER_DESC}" in _listed(view)


def test_a_game_with_no_custom_module_route_gets_dead_buttons_that_do_nothing_when_pressed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The three CMaNGOS games, and this branch's WotLK: no route, dead controls.

    Same rule as `module_sql` and the update check -- a control that is visibly
    unavailable beats one that is pressed and then explains itself. Both
    presses must still be harmless, because the slot is reachable by more than
    the button, and neither may open a dialog it cannot act on.
    """
    services = _services(ps, tmp_path, [])
    services.module_from_link = None
    services.module_from_folder = None
    services.module_install_custom = None
    asked: list[str] = []

    def refuse_link(parent: object, title: str) -> str | None:
        asked.append(title)
        return None

    def refuse_folder(parent: object, title: str) -> Path | None:
        asked.append(title)
        return None

    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=refuse_link,
        folder_asker=refuse_folder,
    )

    assert not view.module_link_button.isEnabled()
    assert not view.module_folder_button.isEnabled()
    view.install_module_from_link()  # must not raise
    view.install_module_from_folder()  # must not raise
    assert asked == []  # not even the dialog opens


def test_removing_a_custom_module_forgets_it_and_removing_a_shipped_one_keeps_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The record follows the folder, and only for the record this app wrote.

    The view never reads a manifest field to tell the two apart: it asks the
    forget seam after the remove returned, and the seam's answer is what
    decides whether the list is re-read. The ordering matters -- a forget
    before the remove would drop the record of a remove that then failed,
    leaving a folder on disk and nothing in the list to try again with.
    """
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services)
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-thing",
    )
    view.install_module_from_link()
    applier = services.applier
    assert isinstance(applier, _FakeApplier)

    view.modules_panel.select("mod-my-thing")
    view._module_action("remove")

    assert route.forgotten == ["mod-my-thing"]
    assert applier.removed == ["mod-my-thing"]
    assert _rows_for(view, "mod-my-thing") == []

    view.modules_panel.select("mod-aoe-loot")
    view._module_action("remove")

    assert route.forgotten == ["mod-my-thing", "mod-aoe-loot"]
    assert _rows_for(view, "mod-aoe-loot") != []


def test_the_custom_install_report_is_the_one_install_selected_prints(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """One formatter, so the rebuild sentence and the pending-SQL lines are the same.

    A second report builder for this route is the mutation this catches: the
    sentence a C++ module gets is the longest piece of copy on the tab and the
    one an operator acts on.
    """
    services = _services(ps, tmp_path, [])
    _with_custom_route(services)
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-thing",
    )

    view.install_module_from_link()

    expected = controller_view_module._format_report(
        ApplyReport(
            "install", "mod-my-thing", family="module", done=("clone",), rebuild_required=True
        )
    )
    assert view.module_report.toPlainText() == expected
    assert "worldserver REBUILD required" in expected


def test_the_wotlk_tab_is_wired_to_derive_install_list_and_forget_a_module_from_a_folder(
    tmp_path: Path,
) -> None:
    """The real bindings behind the two buttons, driven end to end on a scratch install.

    Everything above this test is fake-driven, which proves what the VIEW does
    with the seams and nothing about what `for_entry()` hands it. This one
    takes the services `_for_wotlk()` really builds and walks the folder route
    with no fake in it: lane A's `derive_folder`, lane B's `install(folder=,
    complete=)` over lane A's `copy_folder` and `complete`, the persist under
    `config_dir()/manifests/user/`, the merged store listing it, and `forget`.
    The link seam is asserted to derive only -- installing it would clone.

    The ground is read first: no user layer exists, and the store lists no
    such module, so nothing below is true before the press.
    """
    server_dir = tmp_path / "wotlk"
    server_dir.mkdir()
    (server_dir / (WOTLK.install.password.file or ".db_password")).write_text(
        "hunter2", encoding="utf-8"
    )
    source = tmp_path / "mod-hand-made"
    (source / "src").mkdir(parents=True)
    (source / "conf").mkdir()
    (source / "conf" / "mod_hand_made.conf.dist").write_text(
        "[worldserver]\nHandMade.Enable = 1\n", encoding="utf-8"
    )
    (source / ".git").mkdir()
    (source / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    services = ControllerServices.for_entry(WOTLK, server_dir)
    assert services.module_from_link is not None
    assert services.module_from_folder is not None
    assert services.module_install_custom is not None
    assert services.module_forget is not None
    assert services.store is not None
    user_dir = modules.user_manifests_dir()
    assert not user_dir.exists(), "the ground: no user layer before the first persist"
    assert "mod-hand-made" not in {m.id for m in services.store.load_all("module")}
    assert services.module_from_link("https://github.com/you/mod-linked").id == "mod-linked"

    manifest = services.module_from_folder(source)
    report = services.module_install_custom(manifest, source)

    clone = server_dir / "modules" / "mod-hand-made"
    assert (clone / "src").is_dir()
    assert not (clone / ".git").exists(), "a copy carries no .git"
    assert (server_dir / "env" / "dist" / "etc" / "modules" / "mod_hand_made.conf").is_file()
    assert report.rebuild_required is True
    assert any(line.startswith("copy ") for line in report.done), report.done
    persisted = user_dir / "wow-wotlk" / "modules" / "mod-hand-made.json"
    assert persisted.is_file()
    listed = {m.id: m for m in services.store.load_all("module")}
    assert listed["mod-hand-made"].conf[0].template == "conf/mod_hand_made.conf.dist"
    assert listed["mod-hand-made"].origin is not None
    assert listed["mod-hand-made"].origin.kind == "folder"

    assert services.module_forget(manifest) is True
    assert not persisted.exists()
    assert "mod-hand-made" not in {m.id for m in services.store.load_all("module")}
    assert services.module_forget(services.store.load("module", "mod-aoe-loot")) is False


def test_a_game_with_no_module_checkouts_gets_no_update_button(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The three CMaNGOS games have no `modules/` folder, so the control is dead.

    Same rule as `module_sql`: a control that is visibly unavailable beats one
    that is pressed and then explains itself. A press with nothing wired must
    still be harmless, because `_set_busy(False)` re-enables from the seam.
    """
    services = _services(ps, tmp_path, [])
    services.module_updates = None
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert not view.module_updates_button.isEnabled()
    view.check_module_updates()  # must not raise


def test_an_install_with_nothing_installed_says_so_rather_than_printing_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """An empty answer and a failed read look identical in a blank box."""
    services = _services(ps, tmp_path, [])
    services.module_updates = lambda: ()
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view.check_module_updates()
    assert "No modules are installed" in view.module_report.toPlainText()


def test_the_update_check_route_reaches_this_games_own_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wired callable asks about THIS install's folder, like the SQL route."""
    seen: dict[str, object] = {}

    def fake(server_dir: Path, **kwargs: object) -> tuple[object, ...]:
        seen["server_dir"] = server_dir
        return ()

    monkeypatch.setattr(modules, "module_updates", fake)
    route = ControllerServices.for_entry(WOTLK, tmp_path).module_updates
    assert route is not None
    route()
    assert seen["server_dir"] == tmp_path


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


def test_the_networking_tab_offers_the_loopback_and_a_real_click_selects_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """bug-checklist §41: the third mode has to be reachable from the tab, by a finger.

    Driven with `QTest.mouseClick` on the real radio rather than by calling
    `setChecked()`, because the failure §41 describes is that there is no
    CONTROL — a mode nothing on screen can select is the same bug with a
    `Literal` added to it. The click delivers a press and a release to the
    widget exactly as a user's does, and `network_mode()` is then read for what
    the view would hand `plan()`.

    The view is shown and sized before the click for the reason
    `test_catalog_view.py` records: `mouseClick` aims at the centre of the
    widget's rect, and a control that has never been laid out has none.

    The label is asserted to carry the address because "loopback" is not a word
    a person installing a game server has any reason to know, and the whole
    point of the mode is that its cost is legible before it is chosen. The three
    radios are asserted mutually exclusive: two checked at once would mean the
    new one was added outside the button group, and `network_mode()`'s answer
    would then depend on the order it happens to ask in.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    view.resize(900, 700)
    view.show()
    view._tabs.setCurrentIndex(view._tabs.count() - 1)
    QTest.qWait(50)
    assert view.network_mode() == "lan"

    label = view.loopback_radio.text()
    assert "127.0.0.1" in label, label
    assert "only this computer" in label.lower(), label

    QTest.mouseClick(view.loopback_radio, Qt.MouseButton.LeftButton)
    assert view.network_mode() == "loopback"
    assert view.loopback_radio.isChecked() is True
    assert view.lan_radio.isChecked() is False
    assert view.internet_radio.isChecked() is False

    QTest.mouseClick(view.lan_radio, Qt.MouseButton.LeftButton)
    assert view.network_mode() == "lan"
    assert view.loopback_radio.isChecked() is False
    view.close()


def test_the_loopback_plan_shown_in_the_tab_says_what_it_costs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The sentence the owner reads before pressing Apply, out of the real widget.

    `_format_plan()` renders `NetworkPlan.warnings`, so the cost sentence
    `plan()` attaches to a loopback plan is what has to arrive here. Asserted
    through the view's own text rather than off the plan object, because a
    warning the formatter dropped is a warning nobody ever reads.
    """
    services = _services(ps, tmp_path, [])
    services.network_plan = lambda mode: networking.plan(
        WOTLK, mode, firewall="none", steamos=False, wsl=False, detect_lan=lambda: None
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view.loopback_radio.setChecked(True)
    view.show_network_plan()
    text = view.network_text.toPlainText()
    assert "Mode: loopback" in text, text
    assert "Players set realmlist to: 127.0.0.1" in text, text
    assert "no other machine" in text, text
    assert view.apply_button.isEnabled() is True, "a loopback plan could not be applied"
    view.close()


def test_the_loopback_plan_in_the_tab_offers_to_open_no_ports(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The Apply button under that warning must not also punch holes in ufw.

    Measured on yulon-ubuntu 2026-09-06, before `plan()` had the branch this
    asserts: pressing Apply on a loopback plan added `ufw allow 3724/tcp` and
    `ufw allow 8085/tcp`
    (`.notes/gates/bug41-loopback-2026-09-05/yulon-ubuntu-press/ufw-after-apply.txt`),
    and in that folder's `widget-loopback.log`, line 57 is the warning saying no
    other machine can reach this server, 58 is blank, 59 is `Applied:` and 60 is
    `✓ ufw allow 3724/tcp`. Asserted through the widget's own text because
    that is where a person sees what Apply is about to run; the `lan` half is
    the control, so an empty command list for every mode cannot pass this.
    """
    services = _services(ps, tmp_path, [])
    services.network_plan = lambda mode: networking.plan(
        WOTLK,
        mode,
        firewall="ufw",
        steamos=False,
        wsl=False,
        detect_lan=lambda: "192.168.10.134",
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view.loopback_radio.setChecked(True)
    view.show_network_plan()
    quiet = view.network_text.toPlainText()
    assert "Mode: loopback" in quiet, quiet
    assert "Firewall commands:" not in quiet, quiet
    assert "ufw allow" not in quiet, quiet

    view.lan_radio.setChecked(True)
    view.show_network_plan()
    loud = view.network_text.toPlainText()
    assert "Mode: lan" in loud, loud
    assert "ufw allow 3724/tcp" in loud, loud
    view.close()


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


def test_for_wotlk_wires_the_distro_into_every_seam_that_talks_to_docker(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted-but-ignored parameter is worse than no parameter.

    `for_wotlk()` builds a handful of things that each shell out to docker, and
    a WSL-resident server answers only to its own distro's daemon. The first
    version of this took `wsl_distro` and passed it to none of them, which
    nothing would have reported: the tab would open and every action would
    quietly address the wrong docker.

    The version after THAT passed it to three of them, and this test asserted
    only the controller - so `send_console` and the port scan kept addressing
    the local daemon while a test named "every seam" watched one. Each seam is
    now exercised through the callable the view actually calls, rather than by
    reading an attribute off the object standing next to it.
    """
    entry = load_catalog().get("wow-wotlk")
    services = ControllerServices.for_wotlk(entry, tmp_path, None, "dml-arch")

    assert services.controller.wsl_distro == "dml-arch"

    # The Console tab. `send_command` shells into the world container, which on
    # a WSL-resident server exists only inside the distro.
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        console,
        "send_command",
        lambda cmd, **kw: seen.update(kw) or ConsoleReply(command=cmd, lines=()),
    )
    services.send_console("server info")
    assert seen.get("wsl_distro") == "dml-arch", f"Console addressed the wrong daemon: {seen}"

    # The Networking tab, which reads the published ports to warn about
    # conflicts. Asked of the local daemon it describes a machine the server is
    # not on: it reports conflicts that do not exist and misses the ones that do.
    asked: list[object] = []
    monkeypatch.setattr(
        controller_view_module.docker,
        "published_bindings",
        lambda wsl_distro=None: asked.append(wsl_distro) or {},
    )
    services.network_plan("lan")
    assert asked == ["dml-arch"], f"the port scan addressed the wrong daemon: {asked}"

    # The Modules tab's importer, which is three docker calls in a row — the
    # `compose config` mount probe, the database start, and the one-shot itself.
    # Added when the route got its first call site; the seam scan below found
    # this exact gap the same day, in the binding this lambda goes through.
    ran: dict[str, object] = {}
    monkeypatch.setattr(
        modules,
        "apply_module_sql",
        lambda server_dir, **kw: ran.update(kw) or docker.AttachedRun(0, ()),
    )
    route = services.module_sql
    assert route is not None, "wotlk has an import service and must have the route"
    route(print)
    assert ran.get("wsl_distro") == "dml-arch", f"the importer addressed the wrong daemon: {ran}"


def test_the_maintenance_tab_asks_the_distro_s_docker_what_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backup and restore census the containers, and that census has a daemon too.

    Reported from a WSL-resident install (2026-08-27): the Console tab attached
    and its log streamed, while `Maintenance -> Back up now` answered "could not
    ask Docker what is running ... Docker could not be found on this machine".
    One machine, one daemon, two answers - because the census went to the
    Windows host, which is exactly the box with no Docker on it.

    The seam scan below walked past this: `maintenance` spells "which daemon"
    as an injectable `running` callable rather than as `wsl_distro`, so three
    call sites that pass neither were invisible to a scan that looks for the
    name. Asked here through the callables the view really calls, down to the
    argv the census would have run.
    """
    asked: list[str | None] = []

    def fake_prefix(wsl_distro: str | None = None, *, inside: str | None = None) -> tuple[str, ...]:
        asked.append(wsl_distro)
        return ("wsl.exe", "-d", str(wsl_distro), "--", "docker")

    monkeypatch.setattr(docker.platform, "docker_prefix", fake_prefix)
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    services = ControllerServices.for_wotlk(WOTLK, tmp_path, None, "dml-arch")

    # Nothing is running in this fake, so both calls end in their own ordinary
    # refusal ("ac-database is not running"). What is under test is which
    # daemon was asked before they got there.
    with pytest.raises(MaintenanceError):
        services.backup()
    assert asked == ["dml-arch"], f"the backup census addressed the wrong daemon: {asked}"

    asked.clear()
    plan = services.plan_restore(tmp_path / "there-is-no-such-dump.sql")
    assert asked == ["dml-arch"], f"the restore census addressed the wrong daemon: {asked}"
    assert not any("could not be found on this machine" in r for r in plan.refusals), plan.refusals


def _scan_for_seams_without_a_distro(package: Path, source: Path) -> tuple[set[str], list[str]]:
    """The matching behind the guard below, over any package and any call site.

    Returns `(accepts, missing)`: every name in `package` that takes a
    `wsl_distro`, and every call in `source` that names one of them without
    passing it. A module-level helper rather than a body inline in the guard so
    the regression test below can run THIS code over a synthetic package - a
    second copy of the logic would prove nothing about the guard that ships.
    """
    import ast

    accepts: set[str] = set()
    modules: set[str] = set()
    by_module: dict[str, set[str]] = {}
    defined_in: dict[str, set[str]] = {}
    for path in package.rglob("*.py"):
        modules.add(path.stem)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                defined_in.setdefault(path.stem, set()).add(node.name)
                args = node.args
                if any(a.arg == "wsl_distro" for a in args.args + args.kwonlyargs):
                    # Not dunders: a constructor is spelled by its CLASS name at
                    # the call site, so matching the bare name `__init__` only
                    # ever catches somebody else's `super().__init__(parent)`.
                    if not node.name.startswith("__"):
                        accepts.add(node.name)
                        by_module.setdefault(path.stem, set()).add(node.name)
            elif isinstance(node, ast.ClassDef):
                defined_in.setdefault(path.stem, set()).add(node.name)
                if any(
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == "wsl_distro"
                    for stmt in node.body
                ):
                    accepts.add(node.name)
                    by_module.setdefault(path.stem, set()).add(node.name)

    tree = ast.parse(source.read_text(encoding="utf-8"))
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
            receiver = ast.unparse(func.value)
            # `self.<x>` and `self.services.<x>` are the view calling objects
            # that were built WITH the distro; they are not the seam.
            on_self = receiver.startswith("self")
            # A MODULE-qualified call names exactly one function, and a
            # package-wide set of bare NAMES cannot tell two of them apart.
            # `apply` is both `networking.apply(plan, sql=sql)`, which reaches
            # no daemon and takes no distro, and `sqlplan.apply(...)`, which
            # reaches one and does - so from 7.3 the scan reported the former
            # as a seam addressing the wrong docker.
            #
            # The exemption is only for a name the receiving module DEFINES
            # itself and that definition takes no distro. A name that module
            # merely RE-EXPORTS (`from yulon.apply import DockerSql`) resolves
            # to a definition somewhere else, so it falls through to the
            # package-wide check below - as does a bare name, an
            # object-qualified call, and any receiver that is not a module
            # stem. Resolving locally where it can is what makes this stricter
            # than the bare-name scan; skipping what it cannot resolve would
            # make it looser, and did until 2026-09-01.
            if receiver in modules and name not in by_module.get(receiver, set()):
                if name in defined_in.get(receiver, set()):
                    continue
        elif isinstance(func, ast.Name):
            name, on_self = func.id, False
        else:
            continue
        if name not in accepts or on_self:
            continue
        if not any(k.arg == "wsl_distro" for k in node.keywords):
            missing.append(f"{name}() at {source.name}:{node.lineno}")
    return accepts, missing


def test_every_seam_for_wotlk_builds_says_which_daemon_it_means() -> None:
    """The guard for the test above, so the next seam added cannot be forgotten.

    Four blockers on this branch were one mistake wearing different clothes: a
    function that ACCEPTS `wsl_distro`, called from a site that does not PASS
    one. `Controller` has its own version of this scan. `for_wotlk()` is the
    other place that wires docker to a server, and it held two such call sites
    that six review passes walked straight past.

    Asked of the parse rather than of a spelling: which functions declare the
    parameter, and does each call supply it. A renamed helper stays covered.

    Locally-defined helpers are NOT exempt, and that is the point. The first
    version of this scan skipped any callee defined in this file, which
    exempted `_safe_bindings()` - a helper that takes `wsl_distro` and whose
    caller could forget it. The blocker shape reappeared one level up, inside
    the test written to prevent it (review, 2026-08-26). The cost is that such
    a call must NAME the parameter rather than pass it positionally, which is
    cheap and reads better at the call site anyway.

    DATACLASS FIELDS COUNT TOO, and they did not until 2026-08-31. The dunder
    skip below is right about `__init__` as a NAME, but a dataclass declares
    `wsl_distro` as an annotated class attribute and never writes an `__init__`
    at all - so `DockerSql(...)` and `DockerMysql(...)`, the two seams this
    function builds by hand, were invisible to a scan whose docstring promises
    the next seam cannot be forgotten. Measured: dropping `wsl_distro=` from
    either constructor left the whole suite at 1196 passed. A class whose body
    annotates the field is collected by its CLASS name, which is how the call
    site spells it.

    A MODULE-QUALIFIED CALL IS RESOLVED, AND ONLY WHERE IT RESOLVES. From 7.3
    `X.f()` is exempt when `X.py` defines `f` itself and that definition takes
    no distro - which is what keeps `networking.apply(plan, sql=sql)` out of the
    report. It is NOT exempt when `X` merely re-exports `f` from elsewhere: the
    first version of that rewrite skipped any name it could not find in `X`,
    which handed every re-exported seam a free pass the older bare-name scan
    would have caught. Dormant when found (no such call site existed), fixed
    anyway - see the regression test below.
    """
    package = Path(controller_view_module.__file__).parent.parent
    accepts, missing = _scan_for_seams_without_a_distro(
        package, Path(controller_view_module.__file__)
    )
    assert {
        "send_command",
        "published_bindings",
        # The two dataclass seams, so the collection above cannot rot back to
        # functions-only and still pass.
        "DockerSql",
        "DockerMysql",
    } <= accepts, "the scan found no wsl_distro-aware functions, so it would pass on an empty repo"

    assert not missing, "these address the wrong docker on a WSL-resident server: " + ", ".join(
        missing
    )


def test_the_seam_guard_sees_a_seam_reached_through_a_re_exporting_module(
    tmp_path: Path,
) -> None:
    """A seam re-exported by another module must not walk past the guard above.

    The 7.3 rewrite resolved `X.f()` against the definitions in `X.py` and
    skipped the call when it found none - so a module that does
    `from yulon.apply import DockerSql` and nothing else handed `X.DockerSql()`
    a free pass, on a name the older bare-name scan flagged. That is the one
    direction the guard's own docstring swore it would never move.

    Both shapes live in the same synthetic package, because a fix for one that
    breaks the other is not a fix: `holder.DockerSql(...)` is re-exported and
    must be reported, `networking.apply(...)` is defined locally without a
    distro and must not be.
    """
    package = tmp_path / "synth"
    package.mkdir()
    (package / "apply.py").write_text(
        "class DockerSql:\n    wsl_distro: str | None = None\n", encoding="utf-8"
    )
    # Re-exports the seam and defines nothing of its own - the blind spot.
    (package / "holder.py").write_text(
        "from .apply import DockerSql\n\n__all__ = ['DockerSql']\n", encoding="utf-8"
    )
    # The 7.3 false positive: two different `apply`s, one of them distro-bearing.
    (package / "networking.py").write_text("def apply(plan, sql=None):\n    return sql\n", "utf-8")
    (package / "sqlplan.py").write_text(
        "def apply(spec, *, wsl_distro=None):\n    return spec\n", encoding="utf-8"
    )
    caller = package / "caller.py"
    caller.write_text(
        "from . import holder, networking\n"
        "\n"
        "def build(plan, sql):\n"
        "    networking.apply(plan, sql=sql)\n"
        "    return holder.DockerSql(sql)\n",
        encoding="utf-8",
    )

    accepts, missing = _scan_for_seams_without_a_distro(package, caller)

    assert {
        "DockerSql",
        "apply",
    } <= accepts, f"the synthetic package taught the scan nothing: {accepts}"
    assert missing == ["DockerSql() at caller.py:5"], (
        "line 5 re-exports its seam through `holder`, and the guard must still see it; "
        "line 4 is a local `apply` that takes no distro and must stay exempt. "
        f"got: {missing}"
    )


def test_the_seam_guard_still_exempts_networkings_own_apply() -> None:
    """The 7.3 false positive, pinned by line so the fix above cannot revive it.

    `networking.apply(plan, sql=sql)` at controller_view.py:370 is a different
    `apply` from `sqlplan.apply(..., wsl_distro=...)`; it reaches no daemon.
    Asserted here rather than left implicit in the guard's `not missing`, so a
    regression names the call instead of just reddening the guard - and pinned
    against the parse, so it fails loudly if that call site ever moves.
    """
    import ast

    view = Path(controller_view_module.__file__)
    # Found by parsing rather than pinned to a literal line: the literal was
    # re-pinned by hand three times in one session by edits ABOVE it, which is
    # churn that teaches a reader to update the number without reading what it
    # guards. What is asserted is what the test is named for -- that this exact
    # call is the one the seam guard exempts.
    lines = [
        n.lineno
        for n in ast.walk(ast.parse(view.read_text(encoding="utf-8")))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "apply"
        and ast.unparse(n.func.value) == "networking"
    ]
    assert len(lines) == 1, f"expected exactly one networking.apply call, found {lines}"

    accepts, missing = _scan_for_seams_without_a_distro(view.parent.parent, view)
    assert "apply" in accepts, "the scan no longer knows `apply` can take a distro"
    assert f"apply() at controller_view.py:{lines[0]}" not in missing, missing


def test_for_wotlk_defaults_to_no_distro(qapp: object, tmp_path: Path) -> None:
    """An ordinary local install is unchanged by any of this."""
    entry = load_catalog().get("wow-wotlk")
    assert ControllerServices.for_wotlk(entry, tmp_path).controller.wsl_distro is None


def test_a_cmangos_install_s_account_path_addresses_its_own_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring, not the seam: what `Accounts → Create` really sends for Tortoise.

    `for_wotlk()` builds the `DockerSql` every SQL-backed control uses, and it is
    the one place that holds the catalog entry. Testing `DockerSql(schemas=...)`
    alone would have passed while this call site still handed it nothing — which
    is how `acore_auth` reached a CMaNGOS install in the first place.
    """
    tortoise = load_catalog().get("wow-tortoise")
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        # "" first, so the username reads as free; a row afterwards, so the
        # read-back that follows the INSERT finds the account it just made.
        return subprocess.CompletedProcess(argv, 0, "" if len(seen) == 1 else "1", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    services = ControllerServices.for_wotlk(tortoise, tmp_path, None)
    services.create_account("bob", "hunter2", 0)

    assert seen, "nothing was sent to mysql at all"
    schemas = {argv[-1] for argv in seen}
    assert schemas == {"tw_logon"}, schemas
    assert not any("acore" in " ".join(argv) for argv in seen)


def test_a_cmangos_backup_reaches_that_game_s_own_maintenance_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call site, not the function: which containers the backup censuses.

    This asserted `core_databases == ("tw_logon", "tw_char", "tw_world")` on a
    `wotlk_maintenance.backup()` the view called for every game. Since 7.9 the
    view calls `controller_wow_tortoise.maintenance.backup()`, which binds this
    game's spec and core databases itself and does not accept another game's —
    so the fact under test moved from "did the caller pass the right names" to
    "did the caller reach the right binding". The container it censuses is the
    observable half of that, and it is the half that decides whether a dump
    happens at all.
    """
    tortoise = load_catalog().get("wow-tortoise")
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    with pytest.raises(MaintenanceError) as err:
        ControllerServices.for_entry(tortoise, tmp_path, None).backup()

    assert "tortoise-db" in str(err.value), str(err.value)
    assert "ac-database" not in str(err.value), "the backup censused AzerothCore's containers"


def test_a_cmangos_console_is_sent_its_own_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parser takes the core's prompt; this is the call site that supplies it."""
    tortoise = load_catalog().get("wow-tortoise")
    seen: dict[str, object] = {}

    def fake_send(command: str, **kwargs: object) -> object:
        seen.update(kwargs)
        return ConsoleReply(command, ())

    # Tortoise's console module binds the shared transport by NAME at import,
    # so the patch has to go on that module and not on the one it imported
    # from. Every game's console is exercised together in
    # `test_each_game_s_console_is_sent_its_own_prompt_and_container` below;
    # this one stays because it is the report the prompt fact came from.
    monkeypatch.setattr(tortoise_console, "send_command", fake_send)
    ControllerServices.for_entry(tortoise, tmp_path, None).send_console("server info")
    assert seen.get("prompt") == "mangos>", seen
    assert seen.get("prompt_precedes_answer") is False, seen
    assert seen.get("container") == "tortoise-mangosd", seen


def test_a_core_that_cannot_be_given_an_account_by_sql_says_so_instead_of_failing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Better a disabled button with the working command than one that writes a dead row.

    Every game in the catalog can be given an account by SQL today, so the
    subject here is a synthetic entry rather than a real one: this pins the
    BEHAVIOUR for the next core added before anyone has measured how it stores a
    password. Guessing that wrong does not fail loudly -- it inserts a row that
    looks correct and can never log in -- so the tab refuses and names the
    console command instead.
    """
    catalog = load_catalog()
    for entry in catalog.games:
        assert entry.accounts.scheme is not None, f"{entry.id} lost its scheme"

    unmeasured = WOTLK.model_copy(
        update={"accounts": WOTLK.accounts.model_copy(update={"scheme": None})}
    )
    view = ControllerView(unmeasured, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.create_account_button.isEnabled() is False
    said = view.account_report.text()
    assert "account create" in said, said
    assert "Console" in said, said

    # The measured cores are untouched: AzerothCore, tortoise, and now the two
    # CMaNGOS games whose scheme was solved from rows their own servers wrote.
    for game_id in ("wow-wotlk", "wow-tortoise", "wow-tbc", "wow-vanilla"):
        other = ControllerView(catalog.get(game_id), _services(ps, tmp_path, []), status_poll_ms=0)
        assert other.create_account_button.isEnabled() is True, game_id
        assert other.account_report.text() == "", game_id


def test_a_tortoise_account_is_created_with_that_core_s_own_scheme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scheme decides the COLUMNS a row is written into, and a wrong one is silent.

    Writing AzerothCore's salt/verifier into a table that has neither fails
    loudly; writing this core's `sha_pass_hash` into one that expects SRP6
    succeeds and produces an account that can never log in. Since 7.9 the
    scheme is bound by `controller_wow_tortoise.accounts`, which reads it from
    the same entry, so the fact under test is that the tab reaches THAT writer
    rather than that the tab remembered to pass a scheme.

    The patch goes on the tortoise package's own name for the shared writer:
    it binds it at import, so patching the module it was imported from would
    intercept nothing and this test would pass on the wiring it is here to
    check.
    """
    tortoise = load_catalog().get("wow-tortoise")
    seen: dict[str, object] = {}

    def fake_create(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return AccountResult(username="BOB", account_id=1, created=True, gm_level=0)

    monkeypatch.setattr(tortoise_accounts, "_create_account", fake_create)
    ControllerServices.for_entry(tortoise, tmp_path, None).create_account("bob", "pw", 0)
    assert seen.get("scheme") == "mangos_sha", seen


def test_an_entry_with_no_scheme_is_refused_by_both_create_sites_not_defaulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both wotlk `create_account` call sites passed `entry.accounts.scheme or "azerothcore"`.

    The Accounts tab and the channel's own account are the two seams that create
    a row from this wiring, and both defaulted an entry that declares no scheme
    to AzerothCore's columns — the guess `controller_wow_tortoise.accounts`
    refuses by name, made silently one package over. The tab already disables its
    button for such an entry; this is the layer underneath it, which is what the
    channel presses through.

    The channel's seam is read off the object as `_create` because that is the
    only handle on it: `InstallChannel` takes it as a constructor argument and
    keeps it. It is one of the two call sites this test exists for, so testing
    only the tab's would leave half the fix unpinned.
    """
    unmeasured = WOTLK.model_copy(
        update={"accounts": WOTLK.accounts.model_copy(update={"scheme": None})}
    )
    services = ControllerServices.for_wotlk(unmeasured, tmp_path, None)
    reached: list[object] = []
    monkeypatch.setattr(
        controller_view_module.wotlk_accounts,
        "create_account",
        lambda *args, **kwargs: reached.append(kwargs),
    )

    for create in (
        lambda: services.create_account("bob", "hunter2", 0),
        lambda: services.channel_setup._create("YULON_AB", "hunter2", 0),
    ):
        with pytest.raises(NotImplementedError) as caught:
            create()
        assert "declares no account scheme" in str(caught.value), str(caught.value)
        assert "worldserver console" in str(caught.value), str(caught.value)

    assert reached == [], "the writer was reached with a guessed scheme"


def test_an_entry_with_no_scheme_is_refused_by_the_repair_seam_too_not_defaulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T22: the `create` fix left the `reset=` binding beside it untouched.

    Both lambdas are built from the same `entry` two lines apart; `create`
    passed `checked_scheme(entry.accounts.scheme, entry.id)` and `reset` passed
    nothing, which reached `reset_own_password`'s own keyword default,
    `scheme="azerothcore"`. An entry the create seam above refuses for
    declaring no scheme could still reach the 401 repair path and write
    AzerothCore's `salt`/`verifier` columns into a table that may not have
    them, or that happens to and never authenticates. `channel_setup._reset` is
    the seam under test, for the same reason `_create` was in the sibling test:
    it is the only handle a caller outside this module has on the bound
    callable.
    """
    unmeasured = WOTLK.model_copy(
        update={"accounts": WOTLK.accounts.model_copy(update={"scheme": None})}
    )
    services = ControllerServices.for_wotlk(unmeasured, tmp_path, None)
    reached: list[object] = []
    monkeypatch.setattr(
        controller_view_module.wotlk_accounts,
        "reset_own_password",
        lambda *args, **kwargs: reached.append(kwargs),
    )

    with pytest.raises(NotImplementedError) as caught:
        services.channel_setup._reset("YULON_AB", "hunter2")
    assert "declares no account scheme" in str(caught.value), str(caught.value)
    assert "worldserver console" in str(caught.value), str(caught.value)

    assert reached == [], "the writer was reached with a guessed scheme"


def test_for_wotlk_takes_its_import_gate_from_install_wiring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One wiring for the Catalog tab, the Server tab and the CLI — not three copies.

    Only the GATE moves: the probe pair is the thing all three built by hand,
    and `install_wiring` is where it lives now. The distro travels with it,
    because a WSL-resident server answers only to its own daemon.
    """
    gate_calls: list[tuple[str, object]] = []

    def probe_sentinel() -> docker.ImportState:
        return docker.ImportState("imported", "sentinel", complete=True)

    def reset_sentinel() -> tuple[str, ...]:
        return ()

    def fake_gate(entry: CatalogEntry, *, wsl_distro: str | None = None) -> tuple[object, object]:
        gate_calls.append((entry.id, wsl_distro))
        return probe_sentinel, reset_sentinel

    monkeypatch.setattr(controller_view_module.install_wiring, "import_gate_for", fake_gate)

    services = ControllerServices.for_wotlk(WOTLK, tmp_path, None, "dml-arch")
    assert gate_calls == [(WOTLK.id, "dml-arch")], gate_calls
    assert services.controller.import_probe is probe_sentinel
    assert services.controller.reset_unfinished is reset_sentinel


def test_a_generated_password_game_is_never_handed_the_fixed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this seam closed, asked of the wiring rather than of `db_password()`.

    wow-tbc, wow-vanilla and wow-tortoise all declare `"mode": "generated"`:
    their password is minted at install time and written under the server dir.
    `install_wiring.fixed_db_password()` answers the CATALOGUE's fixed value,
    which for those three is the shared default — so wiring the tab from it
    authenticates as root with the literal "password" and every SQL-backed
    control dies with access denied (closed 2026-08-23, and easy to re-open
    because `for_wotlk` is the one place that reads the file).

    Asked end to end, down to the `MYSQL_PWD` the client is really handed, and
    of BOTH seams: `DockerSql` (accounts, modules, the realmlist update) and
    `DockerMysql` (backup and restore). A test that drove wow-wotlk would pass
    with the substitution in place, because its plan is `fixed`.
    """
    tortoise = load_catalog().get("wow-tortoise")
    assert tortoise.install.password.mode == "generated", "this entry proves nothing otherwise"
    generated = "tortoise-1a2b3c4d5e6f7a8b"
    assert controller_view_module.install_wiring.fixed_db_password(tortoise) != generated
    (tmp_path / str(tortoise.install.password.file)).write_text(generated + "\n", encoding="utf-8")

    handed_to_mysql: list[str] = []
    real_mysql = controller_view_module.wotlk_maintenance.DockerMysql

    def recording_mysql(container: str, password: str, **kwargs: object) -> object:
        handed_to_mysql.append(password)
        return real_mysql(container, password, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(controller_view_module.wotlk_maintenance, "DockerMysql", recording_mysql)

    handed_to_sql: list[str | None] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if isinstance(env, dict) and "MYSQL_PWD" in env:
            handed_to_sql.append(env["MYSQL_PWD"])
        # "" first, so the username reads as free; a row afterwards, so the
        # read-back that follows the INSERT finds the account it just made.
        return subprocess.CompletedProcess(argv, 0, "" if len(handed_to_sql) < 2 else "1", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    services = ControllerServices.for_wotlk(tortoise, tmp_path, None)
    services.create_account("bob", "hunter2", 0)

    assert handed_to_sql, "nothing was sent to mysql at all"
    assert set(handed_to_sql) == {generated}, handed_to_sql
    assert handed_to_mysql == [generated], handed_to_mysql


def test_a_password_file_that_cannot_be_read_still_opens_the_tab_and_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The other half of the same seam: "unknown" is not "use the default", silently.

    `db_password()` answers None when the entry NAMES a file and that file
    cannot be read. The tab is still built — Start and Stop need no database,
    and no tab at all is worse — but the reason every SQL-backed control is
    about to fail is written down once, here, instead of arriving as "access
    denied" six clicks later.
    """
    tortoise = load_catalog().get("wow-tortoise")
    assert not (tmp_path / str(tortoise.install.password.file)).exists()

    with caplog.at_level(logging.WARNING):
        services = ControllerServices.for_wotlk(tortoise, tmp_path, None)

    assert services.controller.spec == tortoise.container_spec(), "no tab at all is worse"
    said = [record.getMessage() for record in caplog.records]
    assert any(
        tortoise.id in message and str(tortoise.install.password.file) in message
        for message in said
    ), said


def test_both_db_seams_for_wotlk_builds_are_bound_to_the_distro_they_live_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AST scan above is static; this is the same question asked of the objects.

    `wsl_distro=` on these two constructors was pinned by NOTHING until now.
    Measured on 2026-08-31: dropping it from `DockerSql(...)`, and separately
    from `DockerMysql(...)`, left the whole suite at 1196 passed either way.
    The seam scan could not see them because a dataclass declares the field
    rather than taking it as a parameter, and every other test that looks like
    it covers this really covers something else - the backup census reads the
    distro from `backup(wsl_distro=...)`, not from the `DockerMysql` it is
    handed, so it passes with the object mis-wired and only the real `docker
    exec` further in would go to the Windows host that has no `ac-database`.

    Asked of the constructed objects, so a call site that passes the parameter
    but hands it `None` is caught as well as one that omits it.
    """
    sql_seams: list[DockerSql] = []
    mysql_seams: list[DockerMysql] = []
    real_sql = controller_view_module.DockerSql
    real_mysql = controller_view_module.wotlk_maintenance.DockerMysql

    def recording_sql(*args: object, **kwargs: object) -> DockerSql:
        made: DockerSql = real_sql(*args, **kwargs)  # type: ignore[arg-type]
        sql_seams.append(made)
        return made

    def recording_mysql(*args: object, **kwargs: object) -> DockerMysql:
        made: DockerMysql = real_mysql(*args, **kwargs)  # type: ignore[arg-type]
        mysql_seams.append(made)
        return made

    monkeypatch.setattr(controller_view_module, "DockerSql", recording_sql)
    monkeypatch.setattr(controller_view_module.wotlk_maintenance, "DockerMysql", recording_mysql)

    ControllerServices.for_wotlk(WOTLK, tmp_path, None, "dml-arch")

    # More than one of each may be built: `import_gate_for()` makes its own
    # pair, and the gate's seams have to address the same daemon as the tab's.
    # Every seam that was built is asked, not just the first.
    assert sql_seams and mysql_seams, "a seam was not built at all"
    assert all(seam.wsl_distro == "dml-arch" for seam in sql_seams), sql_seams
    assert all(seam.wsl_distro == "dml-arch" for seam in mysql_seams), mysql_seams

    sql_seams.clear()
    mysql_seams.clear()
    ControllerServices.for_wotlk(WOTLK, tmp_path)
    assert sql_seams and mysql_seams, "a seam was not built at all"
    assert all(seam.wsl_distro is None for seam in sql_seams), "a distro was invented"
    assert all(seam.wsl_distro is None for seam in mysql_seams), "a distro was invented"


# ------------------------------------------- one package per game (7.9)
#
# Until 7.9 this module imported `controller_wow_wotlk` and used it for every
# install, so a TBC, Vanilla or Tortoise tab drove AzerothCore's package. Each
# test below drives the seam the user actually presses and asks what value came
# out the far end of it — never whether a mapping has a key, which is the shape
# of assertion that let the old wiring pass for four games at once.

CMANGOS_GAMES = ("wow-tbc", "wow-vanilla", "wow-tortoise")
"""The three that were being driven by AzerothCore's package.

Two of them are indistinguishable by family, prompt, account scheme and schema
names — `wow-tbc` and `wow-vanilla` differ only in their container names and
their images — which is why the dispatch is keyed on the id.
"""


def _every_game() -> list[CatalogEntry]:
    return list(load_catalog().games)


def test_every_game_in_the_catalog_can_be_opened(tmp_path: Path) -> None:
    """`for_entry()` refuses an id it has no package for, so nothing may be missing.

    The refusal is the right behaviour and it would reach a user as a tab that
    will not open, so the registry has to cover `catalog.json` and this is
    where that is established — in CI, rather than on the machine of whoever
    installs the fifth game.
    """
    for entry in _every_game():
        services = ControllerServices.for_entry(entry, tmp_path / entry.id)
        assert services.controller.spec == entry.container_spec(), entry.id


def test_a_game_with_no_controller_package_is_refused_by_name(tmp_path: Path) -> None:
    """The fallback that made this bug possible was silent; the refusal names the game."""
    invented = WOTLK.model_copy(update={"id": "wow-cataclysm", "name": "WoW Cataclysm"})

    with pytest.raises(controller_view_module.UnsupportedGameError) as err:
        ControllerServices.for_entry(invented, tmp_path)

    said = str(err.value)
    assert "wow-cataclysm" in said and "WoW Cataclysm" in said, said
    assert "wow-wotlk" in said, "the refusal does not say which games this build can manage"


def test_each_game_gets_the_controller_from_its_own_package(tmp_path: Path) -> None:
    """The four ids reach four different controllers, and each carries its own containers."""
    services = {
        entry.id: ControllerServices.for_entry(entry, tmp_path / entry.id)
        for entry in _every_game()
    }

    assert isinstance(services["wow-tbc"].controller, tbc_controller.TbcController)
    assert isinstance(services["wow-vanilla"].controller, vanilla_controller.VanillaController)
    assert isinstance(services["wow-tortoise"].controller, tortoise_controller.TortoiseController)
    # WotLK is the base class, which is what the other three subclass, so it is
    # asserted by exact type: `isinstance` would be true of all four.
    assert type(services["wow-wotlk"].controller) is Controller

    worlds = {game: s.controller.spec.world for game, s in services.items()}
    assert len(set(worlds.values())) == len(worlds), f"two games share a worldserver: {worlds}"


def test_each_game_waits_for_the_ready_line_its_own_server_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a_world_container_that_answers: None
) -> None:
    """The reason three of the four packages reimplement `wait_ready()` at all.

    `Controller.wait_ready()` builds `docker.azerothcore_ready()`, whose world
    marker is `ready...`. A mangosd never prints it, so a CMaNGOS install
    inheriting that method polls for a line that will never come and answers
    False after the full timeout — the server is up and serving while the app
    says the install never became ready.
    """
    prints = {
        "wow-wotlk": "ready...",
        "wow-tbc": "Avg Diff: 15ms",
        "wow-vanilla": "Avg Diff: 15ms",
        "wow-tortoise": "World initialized in 12 seconds",
    }
    seen: dict[str, docker.ReadySpec] = {}

    def fake_wait(
        spec: docker.ContainerSpec, ready: docker.ReadySpec, *, wsl_distro: str | None = None
    ) -> bool:
        seen[spec.world] = ready
        return True

    monkeypatch.setattr(docker, "wait_ready_for", fake_wait)

    for entry in _every_game():
        services = ControllerServices.for_entry(entry, tmp_path / entry.id)
        services.controller.wait_ready("127.0.0.1", 8085)
        ready = seen[entry.container_spec().world]
        line = prints[entry.id]
        assert re.search(ready.world, line), f"{entry.id} would never see its own {line!r}"
        if entry.id in CMANGOS_GAMES:
            assert not re.search(
                ready.world, docker.AZEROTHCORE_READY_WORLD
            ), f"{entry.id} is still waiting for AzerothCore's ready line"


def test_each_game_s_console_is_sent_its_own_prompt_and_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What `Console -> Send` really hands the transport, for each of the four ids.

    The prompt is not decoration: it is what delimits the reply. Sent `AC>` a
    CMaNGOS server's answer is never found, so every command came back as
    "no console prompt in the reply window" — and `prompt_precedes_answer`
    differs too, so even the right prompt on the wrong side returns an empty
    tuple flagged `prompted=True`.
    """
    seen: dict[str, dict[str, object]] = {}

    def fake_send(command: str, **kwargs: object) -> ConsoleReply:
        seen[str(kwargs.get("container"))] = dict(kwargs)
        return ConsoleReply(command, ())

    # Two patches for one function: tbc and vanilla reach the shared transport
    # through the module, tortoise binds the name at import.
    monkeypatch.setattr(console, "send_command", fake_send)
    monkeypatch.setattr(tortoise_console, "send_command", fake_send)

    for entry in _every_game():
        ControllerServices.for_entry(entry, tmp_path / entry.id).send_console("server info")
        world = entry.container_spec().world
        assert world in seen, f"{entry.id} sent its command to {sorted(seen)} instead"
        kwargs = seen[world]
        assert kwargs["prompt"] == entry.console.prompt, (entry.id, kwargs)
        assert kwargs["prompt_precedes_answer"] is entry.console.prompt_precedes_answer, (
            entry.id,
            kwargs,
        )

    assert (
        seen["tbc-mangosd"]["prompt"] != seen["ac-worldserver"]["prompt"]
    ), "a CMaNGOS console was still sent AzerothCore's prompt"


def test_each_game_s_account_form_addresses_its_own_schema_with_its_own_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What `Accounts -> Create` really runs: which container, which binary, which schema.

    Three per-install facts, all of which were AzerothCore's for every game.
    The schema is why `Unknown database 'acore_auth'` reached CMaNGOS installs;
    the binary is 7.9's `mysql` -> `db.client`, and `mariadb:11` ships no
    `mysql` at all, so the statement failed before it reached a database
    (measured on a live TBC server, 2026-08-26).

    The probe that would otherwise ask the container is answered `None` by
    `conftest._classic_mysql_client_names`, which is the case this change is
    for: with nobody to ask, the name used is the one the catalog declares.
    """
    for entry in _every_game():
        seen: list[list[str]] = []

        def fake_run(
            argv: list[str], __seen: list[list[str]] = seen, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            __seen.append(argv)
            # "" first, so the username reads as free; a row afterwards, so the
            # read-back that follows the INSERT finds the account it just made.
            return subprocess.CompletedProcess(argv, 0, "" if len(__seen) == 1 else "1", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        services = ControllerServices.for_entry(entry, tmp_path / entry.id)
        try:
            services.create_account("bob", "hunter2", 0)
        except AccountError:
            # The fake answers enough rows to build the argv under test, not
            # necessarily enough to finish every scheme's write.
            pass

        assert seen, f"{entry.id} sent nothing to the database at all"
        native = entry.install.native
        assert native is not None, f"{entry.id} has no native block to declare a client"
        for argv in seen:
            client = argv.index("-uroot") - 1
            assert argv[client] == native.db.client, (entry.id, argv)
            assert argv[client - 1] == entry.container_spec().db, (entry.id, argv)
            assert argv[-1] == entry.schema_map()["auth"], (entry.id, argv)


def test_each_game_s_restore_plan_censuses_its_own_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The maintenance binding, asked through the callable the Maintenance tab calls.

    `plan_restore()` refuses by container name — a live worldserver, or a
    database that is not up — and those names are the whole of what the
    per-game binding supplies. Asked with AzerothCore's spec, a CMaNGOS install
    is told `ac-database is not running`, which is true, useless, and names a
    container that will never exist on that machine.
    """
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    for entry in _every_game():
        services = ControllerServices.for_entry(entry, tmp_path / entry.id)
        plan = services.plan_restore(tmp_path / "there-is-no-such-dump.sql")
        refusals = " ".join(plan.refusals)
        assert f"{entry.container_spec().db} is not running" in refusals, (entry.id, refusals)
        if entry.id in CMANGOS_GAMES:
            assert "ac-database" not in refusals, (entry.id, refusals)


def test_a_tab_gets_a_manifest_store_exactly_when_the_catalog_says_it_has_one(
    tmp_path: Path,
) -> None:
    """`has_manifests` is the whole gate, in both directions, and the store must be its OWN.

    `manifests/` held `wow-wotlk` alone until 8.7b added `manifests/wow-tbc/`
    (a CMaNGOS "module" is a conf activation or a SQL mod — there is nothing to
    compile), so this test can no longer say "CMaNGOS means no store". It asks
    the catalog instead, which is what `_no_manifest_store()` warns about when
    the two drift apart.

    The second assertion is the one with teeth. `is not None` would be satisfied
    by a tab handed `wotlk_modules.store()`, which would offer a CMaNGOS server
    twenty-one AzerothCore C++ modules, every one of which would fail at the
    clone or the rebuild. So the store is required to answer with THIS game's
    id, and the ids it serves are required to be disjoint from the other's.
    """
    for entry in _every_game():
        services = ControllerServices.for_entry(entry, tmp_path / entry.id)
        if not entry.has_manifests:
            assert services.store is None and services.applier is None, entry.id
            continue
        assert services.store is not None and services.applier is not None, entry.id
        assert (
            services.store.game == entry.id
        ), f"{entry.id}'s tab was handed {services.store.game}'s manifests"
        assert services.store.game_dir.is_dir(), f"{entry.id} has no manifests/<game>/ on disk"


def test_a_game_that_names_no_import_service_is_offered_no_repair_button(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`repairable` is the database's answer; whether the action can run is the entry's.

    `docker.repair_import()`'s first refusal is that this game never said which
    compose service imports its databases, and only `wow-wotlk` names one. A
    tab that offered the button on `repairable` alone would arm a two-press
    destructive gesture whose only possible outcome is that sentence.

    The probe is forced to the one state that DOES offer a repair, so the only
    thing standing between it and the button is the gate under test.
    """
    tortoise = load_catalog().get("wow-tortoise")
    assert not tortoise.container_spec().import_service, "the premise of this test is gone"

    view = ControllerView(tortoise, _services(ps, tmp_path, []), status_poll_ms=0)
    _watch_repair(view, UNIMPORTED)
    _db_up(view, ps)

    assert view.repair_button.isHidden(), "offered a repair the action could only refuse"
    assert view.repair_label.text() == ""

    # The same probe answer, on the game that DOES name an import service.
    wotlk_view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _watch_repair(wotlk_view, UNIMPORTED)
    _db_up(wotlk_view, ps)
    assert not wotlk_view.repair_button.isHidden(), "the gate hid the one repair that works"


# -- 8.1a: the verdict line, and the first poll ----------------------------


def test_the_tab_reads_its_status_at_once_instead_of_a_poll_interval_later(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Closes `pyplan/bug-checklist.md:552`.

    The timer was started and never fired by hand, so for the first five seconds
    a tab over a running server said "status: unknown" with Start enabled. The
    label's opening value is the tell: nothing else writes it.
    """
    ps.names = "ac-database\nac-authserver\nac-worldserver\n"

    view = ControllerView(
        WOTLK, _services(ps, tmp_path, []), status_poll_ms=5000, job_runner=run_inline
    )

    assert "unknown" not in view.status_label.text()
    assert "world up" in view.status_label.text()


def test_polling_that_is_switched_off_stays_off_including_the_first_read(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`status_poll_ms=0` means "this tab does not poll", not "poll once"."""
    ps.names = "ac-database\n"

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)

    assert view.status_label.text() == "status: unknown"


def test_the_verdict_line_says_the_population_above_the_three_words(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    services = _services(ps, tmp_path, [])
    services.dashboard = lambda: dashboard.Verdict("up", players=3, bots=497)
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    view.refresh_verdict()

    assert view.verdict_label.text() == "up — 3 players, 497 bots"


def test_a_restart_loop_reaches_the_tab_in_those_words(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The half of 8.1a that closes `bug-checklist.md:499` on screen."""
    services = _services(ps, tmp_path, [])
    services.dashboard = lambda: dashboard.Verdict("restart_loop", restarts=4)
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    view.refresh_verdict()

    assert "restart loop" in view.verdict_label.text()
    assert "4 restarts" in view.verdict_label.text()


def test_a_game_whose_verdict_is_not_wired_yet_shows_no_line_at_all(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.1b, 8.1c and 8.1d wire their own; until then the tab is as it was."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)

    view.refresh_verdict()

    assert view.verdict_label.text() == ""
    assert not view.verdict_label.isVisibleTo(view)


def test_a_verdict_that_raises_leaves_the_tab_working(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A dashboard is an instrument; an instrument must not be able to break the tab."""
    services = _services(ps, tmp_path, [])

    def boom() -> dashboard.Verdict:
        raise RuntimeError("the daemon went away")

    services.dashboard = boom
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    view.refresh_verdict()

    assert "could not" in view.verdict_label.text()
    ps.names = "ac-database\n"
    view.refresh_status()
    assert "db up" in view.status_label.text()


def test_a_stop_names_the_file_the_servers_log_was_saved_to(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The evidence is worth nothing if the user cannot find it."""
    services = _services(ps, tmp_path, [])
    saved = tmp_path / "logs" / "wow-wotlk-abc-20260906T180000Z.log"
    services.log_snapshot = _StubRecorder(logsnap.Snapshot(path=saved))
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)
    ps.names = "ac-database\nac-authserver\nac-worldserver\n"

    view.stop_server()

    assert saved.name in view.problem_label.text()


def test_a_stop_whose_snapshot_failed_says_so_rather_than_naming_no_file(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    services = _services(ps, tmp_path, [])
    services.log_snapshot = _StubRecorder(logsnap.Snapshot(problem="the log driver is wedged"))
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)
    ps.names = "ac-database\nac-authserver\nac-worldserver\n"

    view.stop_server()

    assert "wedged" in view.problem_label.text()


def test_the_wotlk_tab_is_wired_with_a_dashboard_and_a_pre_stop_snapshot(
    qapp: object, tmp_path: Path
) -> None:
    """8.1a is WotLK's box, so WotLK's wiring is where the two new seams appear."""
    services = ControllerServices.for_entry(WOTLK, tmp_path)

    assert services.dashboard is not None
    assert isinstance(services.log_snapshot, logsnap.Recorder)
    assert services.controller.pre_stop is services.log_snapshot


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise"])
def test_every_tab_now_has_its_own_dashboard_and_snapshot(
    qapp: object, tmp_path: Path, game: str
) -> None:
    """8.1a–d are all done, so this says every tree rather than all-but-one.

    The seams are the same four times over; what is behind them is each tree's
    own, which is what `test_dbreads.py` and the four gate pages assert. This
    only says nobody was left out — the failure that
    `test_controller_packages_agree.py` exists to catch, from the other side.
    """
    entry = load_catalog().get(game)
    if entry.install.password.file:
        (tmp_path / entry.install.password.file).write_text("hunter2", encoding="utf-8")

    services = ControllerServices.for_entry(entry, tmp_path)

    assert services.dashboard is not None
    assert isinstance(services.log_snapshot, logsnap.Recorder)
    assert services.controller.pre_stop is services.log_snapshot
    assert services.log_snapshot.spec.world == entry.containers.world


def test_the_tbc_tab_is_wired_with_its_own_dashboard_and_snapshot(
    qapp: object, tmp_path: Path
) -> None:
    """8.1b. The seams are the same two; the facts underneath them are this tree's."""
    entry = load_catalog().get("wow-tbc")
    (tmp_path / entry.install.password.file).write_text("hunter2", encoding="utf-8")

    services = ControllerServices.for_entry(entry, tmp_path)

    assert services.dashboard is not None
    assert isinstance(services.log_snapshot, logsnap.Recorder)
    assert services.controller.pre_stop is services.log_snapshot
    assert services.log_snapshot.spec.world == "tbc-mangosd", "it must snapshot THIS tree's world"


# -- 8.2a: the command channel on the tab -----------------------------------


class _StubSetup:
    """Stands in for the channel-setup seam the wiring hands down."""

    def __init__(self, state: object = None, refuse: str = "") -> None:
        self.state = state
        self.refuse = refuse
        self.presses = 0
        self.world_running_when_pressed: list[bool] = []
        self.checks = 0
        self.settles = 0
        self.settled: object = None
        self.repairs = 0
        self.rollbacks = 0
        self.becomes: object = None
        self.repaired: object = None
        self.rolled_back = True

    def enable(self, *, world_running: bool) -> object:
        self.presses += 1
        self.world_running_when_pressed.append(world_running)
        if self.refuse:
            raise channel_setup.EnableRefused(self.refuse)
        return channel_setup.Enabled(path=Path("override.yml"), changed=True)

    def setup_state(self) -> object:
        return self.state

    def check(self) -> object:
        self.checks += 1
        if self.becomes is not None:
            self.state = self.becomes
        return self.state

    def settle(self) -> object:
        self.settles += 1
        if self.settled is not None:
            self.state = self.settled
        return self.state

    def repair(self) -> object:
        self.repairs += 1
        if self.repaired is not None:
            self.state = self.repaired
        return self.state

    def roll_back(self) -> bool:
        self.rollbacks += 1
        return self.rolled_back


def _with_channel(ps: _Ps, tmp_path: Path, stub: _StubSetup) -> ControllerServices:
    services = _services(ps, tmp_path, [])
    services.channel_setup = stub
    return services


def test_the_enable_button_is_offered_only_for_a_game_that_has_a_channel(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.2b, 8.2c and 8.2d wire their own; a tab without one shows no button."""
    plain = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)

    assert plain.enable_channel_button.isVisibleTo(plain) is False


def test_pressing_enable_while_the_world_runs_says_so_and_does_not_write(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The refusal is the feature, so the tab has to carry its sentence."""
    stub = _StubSetup(refuse="the server has to be stopped before the command channel...")
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    ps.names = "ac-database\nac-authserver\nac-worldserver\n"
    view.refresh_status()

    view.enable_channel()

    assert "stopped" in view.problem_label.text()


def test_the_press_is_told_whether_the_world_is_running_rather_than_deciding_alone(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The view knows the status; the module owns the rule. Neither guesses."""
    stub = _StubSetup()
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    ps.names = ""
    view.refresh_status()

    view.enable_channel()

    assert stub.world_running_when_pressed == [False]


def test_after_a_successful_press_the_tab_says_it_is_checked_at_the_next_start(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Nothing is verified yet: the setting is read when the world starts."""
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, _StubSetup()), status_poll_ms=0, job_runner=run_inline
    )

    view.enable_channel()

    said = view.problem_label.text().lower()
    assert "start" in said


def test_a_verified_channel_is_shown_with_the_time_it_was_proved(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubSetup(
        state=channel_setup.Verified(account="YULON_AB", password="pw", at="2026-09-07 01:23 UTC")
    )
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_channel()

    said = view.channel_label.text()
    assert "verified" in said.lower()
    assert "2026-09-07 01:23 UTC" in said, "verified once and verified in March read the same"


def test_a_credential_written_before_times_existed_says_so_rather_than_inventing_one(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubSetup(state=channel_setup.Verified(account="YULON_AB", password="pw"))
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_channel()

    assert "verified" in view.channel_label.text().lower()


def test_a_refused_credential_says_so_and_offers_the_repair(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The repair is offered only where it applies.

    A button that resets the channel account's password is the one control on
    this tab that can break a working channel, so it exists only while the
    server has actually refused the credential.
    """
    stub = _StubSetup(
        state=channel_setup.Refused(
            account="YULON_AB", password="stale", reason="the server did not accept it"
        )
    )
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_channel()

    assert "did not accept" in view.channel_label.text()
    assert view.repair_channel_button.isVisibleTo(view) is True


def test_the_repair_is_hidden_while_the_channel_works(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubSetup(
        state=channel_setup.Verified(account="YULON_AB", password="pw", at="2026-09-07 01:23 UTC")
    )
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_channel()

    assert view.repair_channel_button.isVisibleTo(view) is False


def test_pressing_repair_asks_the_setup_and_shows_what_came_back(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubSetup(
        state=channel_setup.Refused(account="YULON_AB", password="stale", reason="rejected")
    )
    stub.repaired = channel_setup.Verified(
        account="YULON_AB", password="fresh", at="2026-09-07 02:00 UTC"
    )
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.repair_channel()

    assert stub.repairs == 1
    assert "2026-09-07 02:00 UTC" in view.channel_label.text()
    assert view.repair_channel_button.isVisibleTo(view) is False


def test_a_tab_opened_over_a_stale_credential_says_refused_rather_than_verified(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Found by the live gate, which is the only place it could be found.

    A saved credential reads as verified straight off the disk, because that is
    what the file records. If nothing asks the server, a credential the server
    has since stopped accepting keeps saying verified until the next start --
    and the repair the user needs is never offered. So the tab asks once, when
    it opens, off the GUI thread.

    `check()` and not `settle()`: settle CREATES on an install that has none,
    and opening a tab is not permission to write a row into the user's auth
    database.
    """
    stub = _StubSetup(
        state=channel_setup.Verified(
            account="YULON_AB", password="stale", at="2026-09-07 01:23 UTC"
        )
    )
    stub.becomes = channel_setup.Refused(
        account="YULON_AB", password="stale", reason="the server did not accept the saved password"
    )

    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=5, job_runner=run_inline
    )

    assert stub.checks == 1
    assert stub.settles == 0, "opening a tab must never create an account"
    assert "did not accept" in view.channel_label.text()
    assert view.repair_channel_button.isVisibleTo(view) is True


def test_a_tab_told_not_to_poll_still_shows_the_channel_without_asking_the_server(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The same rule the status poll follows: not polled means not polled."""
    stub = _StubSetup(
        state=channel_setup.Verified(account="YULON_AB", password="pw", at="2026-09-07 01:23 UTC")
    )

    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    assert stub.checks == 0
    assert "2026-09-07 01:23 UTC" in view.channel_label.text()


def test_a_fresh_install_settles_its_channel_without_waiting_for_a_start(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install is a press that already wrote the database (T87).

    Until then the first `settle()` came with the next Start, so a tab opened
    straight after an install said "could not ask" on every surface that speaks
    to the world. The tab-open rule (`check()`, never `settle()`) is unchanged:
    this is a separate entry point the window calls once, after `installed`.
    """
    stub = _StubSetup(state=channel_setup.Idle())
    stub.settled = channel_setup.Verified(
        account="YULON_AB", password="pw", at="2026-09-18 08:00 UTC"
    )
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    assert stub.settles == 0, "opening the tab must not settle"
    monkeypatch.setattr(controller_view_module.QTimer, "singleShot", lambda ms, fn: None)

    view.settle_channel_after_install()

    assert stub.settles == 1
    assert "2026-09-18 08:00 UTC" in view.channel_label.text()


def test_a_fresh_install_asks_again_only_while_the_first_answer_is_pending(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second ask exists for a world still logging its bots in, and only then.

    A first `settle()` that timed out leaves `Pending` with the minted password
    in memory; the re-ask verifies it. A first answer that is already Verified
    (or Refused, which has its own button) is not asked again -- a second
    `settle()` on Refused would be a second refusal for the user to read.
    """
    stub = _StubSetup(state=channel_setup.Idle())
    stub.settled = channel_setup.Pending(account="YULON_AB", password="pw", tries=1)
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    scheduled: list[tuple[int, object]] = []
    monkeypatch.setattr(
        controller_view_module.QTimer, "singleShot", lambda ms, fn: scheduled.append((ms, fn))
    )

    view.settle_channel_after_install()
    assert stub.settles == 1
    assert [ms for ms, _ in scheduled] == [controller_view_module._POST_INSTALL_RESETTLE_MS]

    scheduled[0][1]()
    assert stub.settles == 2, "still Pending after the first ask: asked once more"

    stub.settled = channel_setup.Verified(account="YULON_AB", password="pw", at="x")
    stub.state = stub.settled
    scheduled[0][1]()
    assert stub.settles == 2, "Verified is not asked again"


def test_a_tab_torn_down_within_the_minute_is_not_asked_again(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install, then uninstall inside the minute: the deferred ask finds a closed tab.

    `shutdown()` runs before the tab is deleted; a job started after it would
    connect its `done` to a slot of a deleted widget, or run docker against a
    server that is gone.
    """
    stub = _StubSetup(state=channel_setup.Idle())
    stub.settled = channel_setup.Pending(account="YULON_AB", password="pw", tries=1)
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    scheduled: list[tuple[int, object]] = []
    monkeypatch.setattr(
        controller_view_module.QTimer, "singleShot", lambda ms, fn: scheduled.append((ms, fn))
    )
    view.settle_channel_after_install()
    assert stub.settles == 1

    view.shutdown()
    scheduled[0][1]()
    assert stub.settles == 1, "a closed tab must not start a job"


def test_a_finished_start_asks_the_channel_where_it_now_stands(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Otherwise the press writes a configuration nobody ever proves.

    The account is created by the first settle after a start, so without this
    call the channel the user turned on is never set up at all -- and a
    credential that stopped working is never noticed.
    """
    stub = _StubSetup(state=channel_setup.Idle())
    stub.settled = channel_setup.Verified(
        account="YULON_AB", password="pw", at="2026-09-07 02:10 UTC"
    )
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view._server_action_done(None)

    assert stub.settles == 1
    assert "2026-09-07 02:10 UTC" in view.channel_label.text()


def test_a_start_that_fails_on_the_channel_port_rolls_the_channel_back(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The clause about an occupied port, at the seam that learns about it.

    Docker refuses to publish a host port something else holds, so the
    container is never created and no setting is ever read. Undoing the press
    is what makes the next Start work, and saying so is what stops the user
    pressing enable again into the same wall.
    """
    stub = _StubSetup(state=channel_setup.Idle())
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view._start_failed(
        docker.DockerCommandError(
            "driver failed programming external connectivity on endpoint ac-worldserver: "
            "Bind for 127.0.0.1:7878 failed: port is already allocated"
        )
    )

    assert stub.rollbacks == 1
    said = view.problem_label.text()
    assert "7878" in said
    assert "command channel" in said.lower()


def test_a_start_that_fails_for_another_reason_leaves_the_channel_alone(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubSetup(state=channel_setup.Idle())
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view._start_failed(docker.DockerCommandError("ac-database exited with code 1"))

    assert stub.rollbacks == 0
    assert "exited with code 1" in view.problem_label.text()


def test_a_channel_that_gave_up_shows_the_reason_rather_than_a_spinner(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubSetup(
        state=channel_setup.GaveUp(account="YULON_AB", reason="three round trips did not prove it")
    )
    view = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_channel()

    assert "three round trips" in view.channel_label.text()


# -- the interlock ----------------------------------------------------------


def test_a_command_control_is_disabled_while_the_server_is_unstable(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The value 8.1 published, used for the first time here.

    `stable` is False for a restart loop, for a daemon that cannot be asked, and
    — since the TBC gate refuted the first version — for a world whose database
    has gone. Every one of those is a server not to aim a command at.
    """
    services = _with_channel(ps, tmp_path, _StubSetup())
    services.dashboard = lambda: dashboard.Verdict("restart_loop", restarts=4)
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    view.refresh_verdict()

    assert view.enable_channel_button.isEnabled() is False


def test_a_command_control_is_enabled_again_once_the_server_settles(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    services = _with_channel(ps, tmp_path, _StubSetup())
    services.dashboard = lambda: dashboard.Verdict("up", players=0, bots=500)
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    view.refresh_verdict()

    assert view.enable_channel_button.isEnabled() is True


def test_the_press_is_reachable_in_the_one_state_that_allows_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Measured on yulon-win11-gate, 2026-09-07, on the route a person has.

    The press REFUSES while the world is running -- that is 8.2a's whole shape,
    because a failed bind is not atomic and on the CMaNGOS trees it costs
    character saves. The button was enabled on `stable`, and `stable` is only
    ever true while the world IS running. So the control was live exactly when
    pressing it could not work, and dead exactly when it would: with the world
    stopped and the channel not set up, the Windows box showed

        verdict  'stopped'
        channel  'Command channel: not set up yet.'
        ENABLE   disabled

    and the app's own refusal sentence tells the user to do the thing that
    disables the button: "Stop it, press this again, then start it as usual."

    8.2a did not catch it because its gate called `InstallChannel.enable()`
    directly. The mechanism was proved; the route was not.
    """
    services = _with_channel(ps, tmp_path, _StubSetup())
    services.dashboard = lambda: dashboard.Verdict("stopped")
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    view.refresh_verdict()

    assert view.enable_channel_button.isEnabled() is True


def test_the_interlock_reads_stable_rather_than_the_state_word(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A world that is `up` with an unreachable database is not stable.

    Keying off `state == "up"` would pass here, which is exactly the bug the
    TBC gate found in `stable` itself. The interlock must read the property, not
    re-derive it.
    """
    services = _with_channel(ps, tmp_path, _StubSetup())
    services.dashboard = lambda: dashboard.Verdict(
        "up", problem="could not read the server's characters", database_unreachable=True
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    view.refresh_verdict()

    assert view.enable_channel_button.isEnabled() is False


# -- 8.3a: the account list, and the two changes the server makes ------------


class _StubAccounts:
    """Stands in for the account seam the wiring hands down (8.3a)."""

    def __init__(
        self,
        listing: useraccounts.Listing | None = None,
        outcome: useraccounts.Outcome | None = None,
    ) -> None:
        self.list_result = listing or useraccounts.Listing(
            accounts=[
                useraccounts.Account(id=7, username="ALICE", gm_level=0),
                useraccounts.Account(id=9, username="BOB", gm_level=3),
            ]
        )
        self.outcome = outcome or useraccounts.Outcome(True, text="done")
        self.passwords: list[tuple[str, str]] = []
        self.levels: list[tuple[str, int]] = []
        self.listings = 0

    def listing(self) -> useraccounts.Listing:
        self.listings += 1
        return self.list_result

    def set_password(self, account: str, password: str) -> useraccounts.Outcome:
        self.passwords.append((account, password))
        return self.outcome

    def set_gm_level(self, account: str, level: int) -> useraccounts.Outcome:
        self.levels.append((account, level))
        return self.outcome


def _with_accounts(ps: _Ps, tmp_path: Path, stub: _StubAccounts) -> ControllerServices:
    services = _services(ps, tmp_path, [])
    services.accounts = stub
    return services


def test_the_account_list_shows_what_the_read_returned(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubAccounts()
    view = ControllerView(
        WOTLK, _with_accounts(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_accounts()

    said = [view.account_list.item(i).text() for i in range(view.account_list.count())]
    assert any("ALICE" in line for line in said)
    assert any("BOB" in line and "3" in line for line in said)


def test_a_list_that_could_not_be_read_says_so_instead_of_showing_none(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """An empty list and an unreadable database look identical on screen."""
    stub = _StubAccounts(
        listing=useraccounts.Listing(problem="could not read this server's accounts: no container")
    )
    view = ControllerView(
        WOTLK, _with_accounts(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_accounts()

    assert view.account_list.count() == 0
    assert "no container" in view.account_report.text()


def test_neither_change_is_offered_until_an_account_is_chosen(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A button that acts on "whichever row happens to be first" is a trap."""
    view = ControllerView(
        WOTLK,
        _with_accounts(ps, tmp_path, _StubAccounts()),
        status_poll_ms=0,
        job_runner=run_inline,
    )
    view.refresh_accounts()

    assert view.set_password_button.isEnabled() is False
    assert view.set_gm_button.isEnabled() is False

    view.account_list.setCurrentRow(0)

    assert view.set_password_button.isEnabled() is True
    assert view.set_gm_button.isEnabled() is True


def test_setting_a_password_names_the_chosen_account_and_clears_the_field(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The field is cleared for the reason `create_account` clears its own.

    A password left in a widget is a password in every later repr and
    traceback frame of that widget.
    """
    stub = _StubAccounts()
    view = ControllerView(
        WOTLK, _with_accounts(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    view.refresh_accounts()
    view.account_list.setCurrentRow(0)
    view.selected_password.setText("n3w-p@ss")

    view.set_selected_password()

    assert stub.passwords == [("ALICE", "n3w-p@ss")]
    assert view.selected_password.text() == ""


def test_setting_a_level_names_the_chosen_account(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    stub = _StubAccounts()
    view = ControllerView(
        WOTLK, _with_accounts(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    view.refresh_accounts()
    view.account_list.setCurrentRow(1)
    view.selected_gm.setValue(2)

    view.set_selected_gm_level()

    assert stub.levels == [("BOB", 2)]


def test_a_change_the_server_refused_is_shown_in_the_servers_own_words(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubAccounts(outcome=useraccounts.Outcome(False, problem="There is no such account."))
    view = ControllerView(
        WOTLK, _with_accounts(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    view.refresh_accounts()
    view.account_list.setCurrentRow(0)
    view.selected_password.setText("n3w-p@ss")

    view.set_selected_password()

    assert "There is no such account." in view.account_report.text()


def test_a_change_that_worked_re_reads_the_list_so_the_level_shown_is_the_new_one(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Otherwise the tab keeps showing the level the account no longer has."""
    stub = _StubAccounts()
    view = ControllerView(
        WOTLK, _with_accounts(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    view.refresh_accounts()
    before = stub.listings
    view.account_list.setCurrentRow(0)
    view.selected_gm.setValue(1)

    view.set_selected_gm_level()

    assert stub.listings == before + 1


def test_a_game_with_no_account_seam_shows_no_list_and_no_buttons(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.3b, 8.3c and 8.3d each measure their own; a control that cannot work is worse than none."""
    view = ControllerView(
        WOTLK, _services(ps, tmp_path, []), status_poll_ms=0, job_runner=run_inline
    )

    assert view.account_list.isVisibleTo(view) is False
    assert view.set_password_button.isVisibleTo(view) is False


# -- 8.5a: browsing the bots -------------------------------------------------


class _StubBots:
    """Stands in for the bot-browsing seam the wiring hands down (8.5a)."""

    def __init__(self, page: botlist.Page | None = None) -> None:
        self.result = page or botlist.Page(
            bots=[
                botlist.Bot(name="Guglu", level=14, online=True, source="registry"),
                botlist.Bot(name="Ritdy", level=3, online=False, source="prefix"),
            ],
            total=500,
            by_registry=480,
            by_prefix=20,
            next_after=("Ritdy", 9),
        )
        self.asked: list[tuple[int, str]] = []

    def page(self, *, after: tuple[str, int] | None = None, name_like: str = "") -> botlist.Page:
        self.asked.append((after, name_like))
        return self.result


def _with_bots(ps: _Ps, tmp_path: Path, stub: _StubBots) -> ControllerServices:
    services = _services(ps, tmp_path, [])
    services.bots = stub
    return services


def test_the_bot_list_shows_the_rows_and_the_split_by_signal(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Both numbers, because one of them hides the case that matters.

    An install whose prefix changed after its bots were made has rows the
    registry knows and the prefix does not.
    """
    stub = _StubBots()
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_bots()

    said = [view.bot_list.item(i).text() for i in range(view.bot_list.count())]
    assert any("Guglu" in line and "registry" in line for line in said)
    assert any("Ritdy" in line and "prefix" in line for line in said)
    assert "500" in view.bot_summary.text()
    assert "480" in view.bot_summary.text()
    assert "20" in view.bot_summary.text()


def test_a_marker_that_could_not_be_read_says_so_and_shows_no_rows(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubBots(page=botlist.Page(problem="this install's bot marker could not be read"))
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_bots()

    assert view.bot_list.count() == 0
    assert "could not be read" in view.bot_summary.text()


def test_a_marker_matching_nothing_warns_rather_than_reading_as_no_bots(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubBots(
        page=botlist.Page(total=0, warning="no character matched the bot marker 'rndbot'")
    )
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_bots()

    assert "no character matched" in view.bot_summary.text()


# -- 8.5b: the tab on a tree that has only one signal -------------------------


def test_a_tree_with_only_the_prefix_does_not_name_a_registry_it_has_not_got(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.5b. TBC has no playerbots schema, so the split is a sentence about nothing.

    Before this, this tab read "900 bots: 0 by the playerbots registry, 900 by
    the account prefix" on m910q's TBC install — a table this install has not
    got, and a zero beside it that a person would go looking for. The per-row
    `— prefix` suffix is the same noise said 900 times.

    The presence assertions sit beside the absence ones deliberately: `"registry"
    not in ""` is true of a label that says nothing at all.
    """
    stub = _StubBots(
        page=botlist.Page(
            bots=[botlist.Bot(name="Adilad", level=57, online=True, source="prefix")],
            total=900,
            by_registry=0,
            by_prefix=900,
        )
    )
    view = ControllerView(
        TBC, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_bots()

    assert "900 bots" in view.bot_summary.text()
    assert "registry" not in view.bot_summary.text()
    row = view.bot_list.item(0).text()
    assert "Adilad" in row and "level 57" in row
    assert "prefix" not in row


def test_a_marker_matching_nothing_on_a_one_signal_tree_warns_without_naming_a_registry(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.5b, and the clause 8.5a could not reach.

    On WotLK a marker matching nothing still returns the registry's rows, so
    this state is unreachable there — 8.5a's own entry defers it to the trees
    that have only the prefix. This is one of them, and the sentence shown
    beside the zero must not also be a claim about a table that does not exist.
    """
    stub = _StubBots(
        page=botlist.Page(
            total=0,
            warning=(
                "no character matched the bot marker 'NOSUCHBOTPREFIX', though this server "
                "has 901 characters"
            ),
        )
    )
    view = ControllerView(
        TBC, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_bots()

    assert "no character matched" in view.bot_summary.text()
    assert "901" in view.bot_summary.text()
    assert "registry" not in view.bot_summary.text()


def test_a_zero_is_not_the_headline_when_the_marker_is_what_matched_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.5b. The clause is "warns, NEITHER reporting zero" — so it must not.

    The first live run of this path on m910q's TBC install printed
    "0 bots. Page 1, 0 shown. no character matched …": the number a person reads
    first was the one the sentence after it exists to contradict. That is 8.1a's
    confident-lie shape with the correction stapled to the end, and this box is
    the tree the checklist nominates to settle the clause, so what it records
    becomes the rule for 8.5c and 8.5d.
    """
    stub = _StubBots(
        page=botlist.Page(
            total=0,
            warning=(
                "no character matched the bot marker 'NOSUCHBOTPREFIX', though this server "
                "has 901 characters"
            ),
        )
    )
    view = ControllerView(
        TBC, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_bots()

    said = view.bot_summary.text()
    assert said.startswith("no character matched"), said
    assert "0 bots" not in said
    assert "Page 1" in said, "it must still say which page is on screen"


def test_the_next_page_starts_where_this_one_ended(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """A cursor, not a count.

    With OFFSET, one bot logging out before the boundary shifts every later
    page by one: a row is shown twice and the one that took its place is never
    shown at all.
    """
    stub = _StubBots()
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    view.refresh_bots()

    view.next_bot_page()

    assert stub.asked[-1][0] == ("Ritdy", 9)


def test_the_first_page_has_no_previous_to_go_back_to(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    stub = _StubBots()
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    view.refresh_bots()

    view.previous_bot_page()

    assert stub.asked[-1][0] is None
    assert view.previous_bots_button.isEnabled() is False


def test_going_back_returns_to_the_cursor_the_earlier_page_started_from(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Which is why the cursors are kept in a stack.

    There is no arithmetic that turns "where page three starts" into "where
    page two starts": the only way back is the key the earlier page was read
    with.
    """
    stub = _StubBots()
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    view.refresh_bots()
    view.next_bot_page()
    view.next_bot_page()

    view.previous_bot_page()

    assert stub.asked[-1][0] == ("Ritdy", 9)
    assert view.previous_bots_button.isEnabled() is True


def test_a_last_page_offers_no_next(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    stub = _StubBots(
        page=botlist.Page(
            bots=[botlist.Bot(name="Zed", level=1, online=False, source="registry")],
            total=1,
            by_registry=1,
            next_after=None,
        )
    )
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_bots()

    assert view.next_bots_button.isEnabled() is False


def test_a_filter_is_passed_through_and_sends_the_list_back_to_the_first_page(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Otherwise a filter typed on page nine shows page nine of a shorter list."""
    stub = _StubBots()
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    view.refresh_bots()
    view.next_bot_page()
    view.bot_filter.setText("Gug")

    view.filter_bots()

    assert stub.asked[-1] == (None, "Gug")


def test_a_game_with_no_bot_seam_offers_no_tab(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = ControllerView(
        WOTLK, _services(ps, tmp_path, []), status_poll_ms=0, job_runner=run_inline
    )

    assert [view._tabs.tabToolTip(i) for i in range(view._tabs.count())].count("Bots") == 0


# -- 8.6: My Party's surface -------------------------------------------------


class _StubParty:
    """Stands in for `party.InstallParty` — the seam the 8.6 gate script drove.

    The panel's own behaviour is `test_party_panel.py`'s subject; what these
    tests are about is that the tab really hands the panel the seam
    `ControllerServices.my_party` holds, and that a game without one says why.
    """

    def __init__(self) -> None:
        self.added: list[tuple[str, str, str, int | None]] = []
        self.dismissed_all: list[tuple[str, tuple[int, ...]]] = []
        self.members: tuple[party.Member, ...] = ()

    def state(self, master: str) -> party.PartyState:
        return party.PartyState(
            True, "", (party.Precondition("bridge_answered", True, ""),), members=self.members
        )

    def specs(self, klass: str) -> tuple[str, ...]:
        return ("fire pve",) if klass == "mage" else ()

    def max_level(self) -> int | None:
        return 80

    def add(
        self,
        master: str,
        klass: str,
        *,
        gender: str = "",
        spec: str = "",
        level: int | None = None,
    ) -> party.Addition:
        self.added.append((master, klass, spec, level))
        return party.Addition(True, True, "Jilsur", True, True, "Jilsur joined the party.")

    def remove(self, master: str, bot: str) -> party.Dismissal:
        return party.Dismissal(True, True, f"{bot} left the party.", bot=bot)

    def remove_all(self, master: str, confirmed: tuple[int, ...]) -> party.MassDismissal:
        self.dismissed_all.append((master, confirmed))
        return party.MassDismissal(
            1,
            (party.Dismissal(True, True, "Jilsur left the party.", bot="Jilsur"),),
            "1 bot left the party: Jilsur.",
        )


def _with_party(ps: _Ps, tmp_path: Path, seam: _StubParty) -> ControllerServices:
    services = _with_bots(ps, tmp_path, _StubBots())
    services.my_party = seam
    return services


def test_my_party_is_on_the_bots_tab_and_a_press_reaches_the_seam(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The clause this box exists for: not reachable only from a script.

    Every press in `.notes/gates/8.6-wotlk-yulon-ubuntu2-2026-09-09/` went
    through `gate86b.py`, which is what the exit review calls out
    (`pyplan/phase8-exit-review-2026-09-09.md`, clause 3). This is the same seam
    with a button on it.
    """
    seam = _StubParty()
    view = ControllerView(
        WOTLK, _with_party(ps, tmp_path, seam), status_poll_ms=0, job_runner=run_inline
    )

    assert "Bots" in [view._tabs.tabToolTip(i) for i in range(view._tabs.count())]
    assert view.party_panel is not None
    assert seam.added == [], "the ground: nothing has been asked of the server yet"
    view.party_panel.character.setText("Pakka")
    view.party_panel.klass.setCurrentText("mage")
    view.party_panel.add_bot()

    assert seam.added == [("Pakka", "mage", "", None)]
    # The seam's own sentence, read back off the panel. Without this the test
    # passes on a press that RAISED: `run_inline` routes any exception to the
    # panel's report, and the stub has already recorded the call by then. It
    # passed exactly that way once — `party` was not imported in this file, so
    # the stub's own return value was a `NameError` and nothing said so.
    assert view.party_panel.report.text() == "Jilsur joined the party."


def test_the_spec_the_level_and_dismiss_all_reach_the_tabs_own_seam(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T5's three controls, through the object `ControllerServices.my_party` holds.

    The panel's own behaviour is `test_party_panel.py`'s subject; what this is
    about is that all three really are wired to the tab's seam and not to
    something the panel made for itself — the spec list and the level bound are
    readings of an INSTALL, and a panel that produced either on its own would be
    a widget that had gone looking for a server folder.
    """
    seam = _StubParty()
    seam.members = (party.Member("Jilsur", 948, 8, 1),)
    view = ControllerView(
        WOTLK, _with_party(ps, tmp_path, seam), status_poll_ms=0, job_runner=run_inline
    )
    assert view.party_panel is not None
    view.party_panel.character.setText("Pakka")
    view.party_panel.klass.setCurrentText("mage")
    view.party_panel.spec.setCurrentText("fire pve")
    view.party_panel.level.setValue(60)

    view.party_panel.add_bot()

    assert seam.added == [("Pakka", "mage", "fire pve", 60)], "the spec picker read the seam's list"
    assert view.party_panel.level.maximum() == 80, "the bound is the seam's, not this panel's"
    view.party_panel.refresh_party()
    view.party_panel.dismiss_all()
    assert seam.dismissed_all == [], "the ground: the first press only arms"

    view.party_panel.dismiss_all()

    assert seam.dismissed_all == [("Pakka", (948,))], "the confirmed guid reached the tab's seam"
    assert view.party_panel.report.text() == "1 bot left the party: Jilsur."


def test_a_game_with_no_party_route_says_why_rather_than_showing_a_dead_panel(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The three CMaNGOS games get the reason, which is the whole surface there.

    Owner decision, 2026-09-06 (`pyplan/phase8-parity-decisions.md:41`): the
    route is `mod-ale` plus `mod-playerbots`' `addclass`, both AzerothCore, so
    there is nothing to wire — and a control that sent those commands at a
    server that has never heard of them would be worse than none.
    """
    view = ControllerView(
        TBC, _with_bots(ps, tmp_path, _StubBots()), status_poll_ms=0, job_runner=run_inline
    )

    assert view.party_panel is None
    assert "WoW WotLK only" in view.my_party_absent.text()


def test_a_finished_party_press_re_reads_the_bot_list(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A bot that just joined a party is a row the Browse list has not got yet.

    The cross-link the users-surface design names
    (`.notes/phase8-designs/b-users-surface.md:111`).
    """
    bots = _StubBots()
    services = _with_bots(ps, tmp_path, bots)
    services.my_party = _StubParty()
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)
    assert view.party_panel is not None
    view.party_panel.character.setText("Pakka")
    asked_before = len(bots.asked)

    view.party_panel.add_bot()

    assert len(bots.asked) == asked_before + 1
    assert view.party_panel.report.text() == "Jilsur joined the party.", (
        "the press must have SUCCEEDED: `party_changed` fires on a failure too, so "
        "without this the re-read is proved by a press that raised"
    )


def test_one_bot_is_a_bot_and_not_one_bots(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """A filter that matched one row said "1 bots" on the live gate."""
    stub = _StubBots(
        page=botlist.Page(
            bots=[botlist.Bot(name="Anmi", level=7, online=False, source="registry")],
            total=1,
            by_registry=1,
        )
    )
    view = ControllerView(
        WOTLK, _with_bots(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )

    view.refresh_bots()

    assert "1 bot:" in view.bot_summary.text()


def test_a_change_whose_result_is_unknown_is_not_announced_as_a_failure(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The sentence is shown; the failure signal is not raised.

    `action_failed` is what the rest of the app treats as "that did not
    happen". A timeout on a password change is not that — the command may have
    run — so the tab says so in words and stays quiet on the wire.
    """
    stub = _StubAccounts(
        outcome=useraccounts.Outcome(
            False,
            indeterminate=True,
            problem="the server did not answer within 20s. The change may already have been made",
        )
    )
    view = ControllerView(
        WOTLK, _with_accounts(ps, tmp_path, stub), status_poll_ms=0, job_runner=run_inline
    )
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.refresh_accounts()
    view.account_list.setCurrentRow(0)
    view.selected_password.setText("n3w-p@ss")

    view.set_selected_password()

    assert "may already have been made" in view.account_report.text()
    assert failures == []


# -- 8.2e: the tree with no listener to set up -------------------------------


def _attach_only() -> CatalogEntry:
    """A tree whose entry says `attach`: the shape Tortoise had until 2026-09-08.

    Tortoise was the real example while its mangosd had no SOAP (8.2e). The fork
    re-added the interface and the pin moved onto it, so no shipped entry is
    attach-only any more -- and a test asserting the console-only shape against
    a real entry would start asserting that the channel is missing from the tree
    it was just added to. The same move `test_channel_enable.py` made for the
    no-block refusal: pin the shape against a synthetic entry.
    """
    tortoise = load_catalog().get("wow-tortoise")
    return tortoise.model_copy(update={"operations": Operations(channel="attach")})


def test_a_console_channel_says_so_instead_of_offering_a_button(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A core with no listener: there is nothing to switch on.

    A greyed-out "Turn on the command channel" would be the worst of both --
    it says the feature exists and refuses to explain. The tab carries the
    reason instead, and the button does not exist on such an entry at all.
    """
    tortoise = _attach_only()
    services = _services(ps, tmp_path, [])
    view = ControllerView(tortoise, services, status_poll_ms=0, job_runner=run_inline)

    view.refresh_channel()

    assert view.enable_channel_button.isVisibleTo(view) is False
    assert view.repair_channel_button.isVisibleTo(view) is False
    said = view.channel_label.text()
    assert view.channel_label.isVisibleTo(view) is True, said
    assert "console" in said.lower(), said
    assert (
        "not set up yet" not in said.lower()
    ), "that sentence promises a set-up that cannot happen"


def test_the_soap_trees_keep_their_button(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The control, without which the test above would pass on a build with no buttons."""
    services = _with_channel(ps, tmp_path, _StubSetup())
    for game in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        soap = ControllerView(
            load_catalog().get(game), services, status_poll_ms=0, job_runner=run_inline
        )
        assert soap.enable_channel_button.isVisibleTo(soap) is True, game
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    assert view.enable_channel_button.isVisibleTo(view) is True


class _Probe:
    """Stands in for the console channel the tab is handed."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.sent: list[str] = []

    def __call__(self, command: str) -> object:
        self.sent.append(command)
        return self.answer


def _with_probe(ps: _Ps, tmp_path: Path, probe: _Probe) -> ControllerServices:
    services = _services(ps, tmp_path, [])
    services.console_probe = probe
    return services


def test_the_console_probe_button_belongs_to_the_console_trees_alone(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A tree with a set-up button does not also need a test button.

    Its verified line already says a real round trip answered, and with a time
    on it. The console trees have no such line to show, which is what this
    button is for.
    """
    tortoise = _attach_only()
    probe = _Probe(channel.Answer(outcome="yes", text="Tortoise 1.18.1"))

    console = ControllerView(
        tortoise, _with_probe(ps, tmp_path, probe), status_poll_ms=0, job_runner=run_inline
    )
    soap = ControllerView(
        WOTLK, _with_channel(ps, tmp_path, _StubSetup()), status_poll_ms=0, job_runner=run_inline
    )

    assert console.test_console_button.isVisibleTo(console) is True
    assert soap.test_console_button.isVisibleTo(soap) is False


def test_the_probe_shows_what_the_console_answered(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The visible effect this box asks for: a real reply, on the Server tab."""
    tortoise = _attach_only()
    probe = _Probe(channel.Answer(outcome="yes", text="Tortoise 1.18.1\nOnline players: 0"))
    view = ControllerView(
        tortoise, _with_probe(ps, tmp_path, probe), status_poll_ms=0, job_runner=run_inline
    )

    view.test_console()

    assert probe.sent == [commands.SERVER_INFO]
    assert "Online players: 0" in view.console_probe_label.text()


def test_a_window_with_no_prompt_reads_as_could_not_ask_on_the_tab(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The clause this whole box turns on, said in the words a person reads.

    `indeterminate` means the command may have run. Presenting that as a
    failure would invite somebody to send it again -- which for a mutation is
    exactly the wrong advice, and is why this tree is offered no mutations yet.
    """
    tortoise = load_catalog().get("wow-tortoise")
    probe = _Probe(
        channel.Answer(
            outcome="unknown",
            text="Loading maps...",
            reason="the console printed no prompt inside the reply window",
            indeterminate=True,
        )
    )
    view = ControllerView(
        tortoise, _with_probe(ps, tmp_path, probe), status_poll_ms=0, job_runner=run_inline
    )

    view.test_console()

    said = view.console_probe_label.text().lower()
    assert "no prompt" in said, said
    # ONCE, not merely present. The live gate on m910q read this sentence back
    # with "the command may still have run" in it twice: the channel's reason
    # said it and this label said it again. The channel states what happened
    # and `indeterminate` carries what it implies; the wording of the
    # implication belongs to whatever shows it to a person.
    assert said.count("may still have run") == 1, said
    # ...and it reads as prose. The first fix for the doubling left the two
    # sentences run together with no full stop between them.
    assert ". the command may still have run" in said, said
    # Not a substring search for "fail": the sentence legitimately contains the
    # word, in "nothing here is a failure". What must not appear is the CLAIM.
    for claim in ("the command failed", "failed to", "could not run"):
        assert claim not in said, said
    assert said.startswith("could not ask"), said


def test_a_game_that_knows_where_its_levels_live_gets_the_accounts_surface(
    tmp_path: Path,
) -> None:
    """The catalog fact and the wiring are two things, and only one of them shows.

    `accounts.level` says this tree's GM level store has been measured on its
    own box. If the services for that game are then assembled without an
    accounts object, the Accounts tab draws its "this game cannot do that yet"
    sentence — for a game that can, with the measurement sitting in the catalog
    unused. Nothing raises; the feature is just missing (8.3c found exactly
    this on Vanilla, whose level block was measured the same afternoon).

    The reverse arm matters as much: a game with no measured store must NOT be
    handed the surface, because reading the wrong store reports every account
    as level 0.
    """
    for entry in _every_game():
        services = ControllerServices.for_entry(entry, tmp_path / entry.id)
        measured = entry.accounts.level is not None
        assert (services.accounts is not None) is measured, (
            f"{entry.id}: level block {'measured' if measured else 'absent'}, "
            f"accounts surface {'present' if services.accounts else 'absent'}"
        )


@pytest.mark.parametrize("game", [e.id for e in load_catalog().games])
def test_the_gm_level_controls_offer_what_this_tree_actually_accepts(
    game: str, tmp_path: Path
) -> None:
    """A hard-coded 0-to-3 was drawn for every game until 8.3d.

    The Tortoise fork accepts 4 -- measured on the live server, where
    `account set gmlevel SHAPROBE 4` answered *"You change security level of
    account SHAPROBE to 4."* and `5` answered *"Incorrect values."* Its own
    check grants at the caller's own level rather than strictly below it, so
    drawing 0-to-3 there hides a level the tree has, and hides it silently:
    nothing fails, the person simply cannot ask for it.

    Both controls are checked, because there are two -- one for creating an
    account and one for changing an existing one -- and they were two separate
    hard-coded numbers.
    """
    entry = load_catalog().get(game)
    view = ControllerView(entry, _services(_Ps(), tmp_path, []), status_poll_ms=0)
    level = entry.accounts.level
    ceiling = level.max_level if level is not None else 3

    assert view.account_gm.maximum() == ceiling, game
    assert view.selected_gm.maximum() == ceiling, game
    assert view.account_gm.minimum() == 0, game


# -- 8.5c: what each game's Bots tab really asks -------------------------------


BOT_SQL_BY_GAME = {
    # game -> (auth schema, characters schema, the db container the query runs in)
    "wow-wotlk": ("acore_auth", "acore_characters", "ac-database"),
    "wow-tbc": ("realmd", "characters", "tbc-db"),
    "wow-vanilla": ("realmd", "characters", "vanilla-db"),
    "wow-tortoise": ("tw_logon", "tw_char", "tortoise-db"),
}
"""Written out per game rather than read back out of the entry.

An expectation derived from `entry.schema_map()` is the same object that
produced the value under test, so it passes on a wrong catalog value — which is
the class of defect this test exists to catch. These four were each measured on
their own box: `acore_*` on yulon-ubuntu (8.1a), `realmd`/`characters` from
m910q's TBC and Vanilla volumes (8.1b, 8.1c), `tw_logon`/`tw_char` from the
Tortoise install (8.1d).
"""


def test_each_game_browses_bots_in_its_own_schemas_and_names_no_registry_it_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Bots tab, driven for all four games through the seam that runs the SQL.

    Until 8.5c only WotLK's SQL had ever been asserted at the wiring level; the
    other three were checked by calling `botlist.page()` with a hand-picked
    entry, which is a call site rather than the function the button reaches
    ("reviews check functions, not call sites"). 8.3c found precisely this class
    of defect on Vanilla by walking every game instead.

    Two seams are answered for, and both are named on purpose. `docker_prefix`
    decides whether `DockerSql._argv` can build a command at all, and
    `_probe_client` is the one that would otherwise run `docker exec` against
    this laptop's daemon just to ask which mysql binary a container has — a
    probe nobody reading "fake `subprocess.run`" would think to stop. The client
    cache is module-level and is cleared between games, because a name resolved
    for one container must not answer for the next.
    """
    from yulon import apply as apply_module

    sent: dict[str, list[list[str]]] = {}

    monkeypatch.setattr(apply_module.platform, "docker_prefix", lambda wsl_distro=None: ("docker",))
    monkeypatch.setattr(apply_module, "_probe_client", lambda container, candidates: candidates[0])

    for entry in _every_game():
        apply_module._client_cache.clear()
        argvs: list[list[str]] = []
        asked: list[str] = []
        sent[entry.id] = argvs

        # The two lists are bound as defaults rather than closed over: this
        # function is defined inside the loop, and a closure would read
        # whichever list the LAST game happened to leave behind, so every
        # game's assertions would be made against the last game's traffic.
        def fake_run(
            argv: Sequence[str],
            _argvs: list[list[str]] = argvs,
            _asked: list[str] = asked,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            _argvs.append(list(argv))
            # The statement is on stdin, never in argv -- `apply._mysql` puts it
            # there deliberately, because argv is world-readable and a statement
            # can carry a password. The first version of this test read argv for
            # the SQL and found the schema name and nothing else.
            _asked.append(str(kwargs.get("input") or ""))
            return subprocess.CompletedProcess(list(argv), 0, "0\t0\t0", "")

        monkeypatch.setattr(apply_module.subprocess, "run", fake_run)
        server_dir = tmp_path / entry.id
        (server_dir / "etc").mkdir(parents=True, exist_ok=True)
        services = ControllerServices.for_entry(entry, server_dir)
        assert services.bots is not None, f"{entry.id} has no Bots tab"
        services.bots.page()

        auth, characters, container = BOT_SQL_BY_GAME[entry.id]
        counting = next((s for s in asked if s.startswith("SELECT COUNT(*), SUM(")), "")
        assert counting, f"{entry.id} never counted: {asked}"
        assert f"FROM {characters}.characters" in counting, f"{entry.id}: {counting}"
        assert f"FROM {auth}.account" in counting, f"{entry.id}: {counting}"
        assert all(a[-1] == characters for a in argvs), f"{entry.id} connected elsewhere: {argvs}"
        assert all(container in a for a in argvs), f"{entry.id} asked the wrong container: {argvs}"

        registry = entry.observability.bots.registry
        named = "playerbots_account_type" in counting
        assert named is (registry is not None), (
            f"{entry.id}: registry {'declared' if registry else 'absent'}, "
            f"table {'named' if named else 'not named'} in {counting}"
        )


# ------------------------------------------------------- 8.9a: the uninstall
#
# The view's half of the box: the plan has to be on screen before the button
# will act, the checkbox is what reaches `run()`, and the removal is signalled
# UP so the window can drop the tab and the Catalog tile can go back to
# "Install". Driven through the same run seam as everything else on this tab.


class _FakeUninstall:
    """A `purge.Uninstaller` double: records what it was asked, answers what it was told."""

    def __init__(
        self,
        server_dir: Path,
        *,
        refusal: str = "",
        failure: str = "",
        report: object | None = None,
        while_running: object | None = None,
        forget_error: OSError | None = None,
    ) -> None:
        self.server_dir = server_dir
        self.refusal = refusal
        self.failure = failure
        self.plans = 0
        self.runs: list[bool] = []
        self.busy_seen: list[object] = []
        self.forgets = 0
        self._report = report
        self._while_running = while_running
        self._forget_error = forget_error

    def forget(self) -> None:
        """`Uninstall.forget` (T34): the same attribute `main.py` replaces in the app."""
        self.forgets += 1
        if self._forget_error is not None:
            raise self._forget_error

    def plan(self) -> purge.PurgePlan:
        self.plans += 1
        if self.refusal:
            return purge.PurgePlan(
                game="wow-wotlk", server_dir=self.server_dir, refusal=self.refusal
            )
        return purge.PurgePlan(
            game="wow-wotlk",
            server_dir=self.server_dir,
            project="yulon-wow-wotlk-deadbeef",
            containers=("ac-worldserver", "ac-database"),
            volumes=(
                "yulon-wow-wotlk-deadbeef_db-data",
                "yulon-wow-wotlk-deadbeef_client-data",
            ),
            character_volume="yulon-wow-wotlk-deadbeef_db-data",
            client_volume="yulon-wow-wotlk-deadbeef_client-data",
            images=("yulon.local/ac-wotlk-worldserver:native-deadbeef",),
            folder_bytes=2_300_000_000,
        )

    def run(self, *, keep_characters: bool) -> purge.PurgeReport:
        self.runs.append(keep_characters)
        if self._while_running is not None:
            self.busy_seen.append(self._while_running())
        if self.failure:
            raise purge.PurgeError(self.failure)
        return self._report or purge.PurgeReport(
            removed_containers=True,
            removed_volumes=("yulon-wow-wotlk-deadbeef_client-data",),
            kept_volumes=("yulon-wow-wotlk-deadbeef_db-data",) if keep_characters else (),
            folder_removed=True,
            record_forgotten=True,
        )


def _uninstall_view(ps: _Ps, tmp_path: Path, fake: _FakeUninstall) -> ControllerView:
    services = _services(ps, tmp_path, [])
    services.uninstall = fake
    return ControllerView(WOTLK, services, status_poll_ms=0)


def test_the_uninstall_button_will_not_act_until_its_plan_is_on_screen(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The restore action's gate, not the typed-name box the decisions doc assumed.

    `phase8-decisions.md`:172 says the typed confirmation "is the same pattern
    the restore action already uses" — and it is not: restore's gate is that the
    PLAN must be on screen first (`run_restore()` refuses with "Show the restore
    plan first."). Uninstall has a `plan()` too, so it inherits the gate this
    tab actually has rather than inventing a second confirmation idiom.
    """
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    view.run_uninstall()
    assert fake.runs == [], "it removed a server nobody had been shown a plan for"
    assert "Show the uninstall plan first" in view.uninstall_label.text()


def test_a_plan_that_refuses_shows_the_refusal_and_offers_no_uninstall(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A refusal is the whole answer, and there must be nothing left to press."""
    fake = _FakeUninstall(tmp_path, refusal="ac-worldserver: still running. Stop the server first.")
    view = _uninstall_view(ps, tmp_path, fake)
    view.show_uninstall_plan()
    assert "Stop the server first" in view.uninstall_label.text()
    assert view.uninstall_confirm_button.isHidden()
    view.run_uninstall()
    assert fake.runs == []


def test_the_plan_names_the_folder_and_its_size_before_anything_is_pressed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    view.show_uninstall_plan()
    text = view.uninstall_label.text()
    assert str(tmp_path) in text
    assert "2.1 GB" in text or "2.3 GB" in text, text
    assert not view.uninstall_confirm_button.isHidden()


def test_keep_my_characters_is_unticked_by_default_and_is_what_reaches_run(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Owner answer 2: one button with a checkbox, unticked. Not two buttons."""
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    assert view.keep_characters_check.isChecked() is False
    view.show_uninstall_plan()
    view.run_uninstall()
    assert fake.runs == [False]

    view.keep_characters_check.setChecked(True)
    view.show_uninstall_plan()
    view.run_uninstall()
    assert fake.runs == [False, True]


def test_a_ticked_uninstall_says_where_the_kept_database_password_went(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The promise on screen is "reinstall and your characters are there".

    On a `generated` entry that promise rests on a file this action wrote into
    Yu'lon's config directory, because the one inside the folder went with the
    folder. The user is told which file: it is the thing that has to travel
    with a moved config directory, and the thing worth a backup.
    """
    kept = tmp_path / "config" / "db-secrets" / "wow-vanilla-deadbeef.json"
    fake = _FakeUninstall(
        tmp_path,
        report=purge.PurgeReport(
            kept_volumes=("yulon-wow-vanilla-deadbeef_db-data",),
            secret_kept=kept,
            folder_removed=True,
            record_forgotten=True,
        ),
    )
    view = _uninstall_view(ps, tmp_path, fake)
    view.keep_characters_check.setChecked(True)
    view.show_uninstall_plan()
    view.run_uninstall()
    text = view.uninstall_label.text()
    assert "find those characters again" in text
    assert str(kept) in text


def test_the_removal_is_signalled_up_with_the_game_and_the_folder(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The window needs both to find the tab, which is keyed by (game, server dir)."""
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    seen: list[tuple[str, object]] = []
    view.uninstalled.connect(lambda game, folder: seen.append((game, folder)))
    view.show_uninstall_plan()
    view.run_uninstall()
    assert seen == [("wow-wotlk", tmp_path)]


def test_an_uninstall_that_failed_says_so_and_signals_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A tab dropped after a failed removal would take the only surface with it."""
    fake = _FakeUninstall(tmp_path, failure="the folder could not be deleted")
    view = _uninstall_view(ps, tmp_path, fake)
    seen: list[object] = []
    failures: list[str] = []
    view.uninstalled.connect(lambda game, folder: seen.append(game))
    view.action_failed.connect(failures.append)
    view.show_uninstall_plan()
    view.run_uninstall()
    assert seen == []
    assert "could not be deleted" in view.uninstall_label.text()
    assert failures and "could not be deleted" in failures[0]


def test_a_failed_uninstall_makes_the_user_ask_for_a_fresh_plan(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The machine changed under the plan, so the photograph is no longer evidence."""
    fake = _FakeUninstall(tmp_path, failure="the folder could not be deleted")
    view = _uninstall_view(ps, tmp_path, fake)
    view.show_uninstall_plan()
    view.run_uninstall()
    view.run_uninstall()
    assert fake.runs == [False], "a second press acted on a plan that had already failed"


def test_the_tab_reports_itself_busy_while_the_uninstall_runs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`busy_reason()` is what stops the window destroying a running QThread.

    A purge is a long blocking job in a `_JobWorker`, and a tab torn down under
    one is the 0xC0000409 abort `drop_controller()` and this function exist to
    prevent.
    """
    view: ControllerView | None = None
    fake = _FakeUninstall(tmp_path, while_running=lambda: view.busy_reason())
    view = _uninstall_view(ps, tmp_path, fake)
    assert view.busy_reason() is None
    view.show_uninstall_plan()
    view.run_uninstall()
    assert fake.busy_seen and fake.busy_seen[0] is not None, fake.busy_seen
    assert "uninstall" in str(fake.busy_seen[0]).lower()
    assert view.busy_reason() is None, "the tab stayed busy after the job finished"


def test_a_second_press_cannot_delete_what_the_first_promised_to_keep(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The uninstall controls lock while the purge runs, and the slot refuses too.

    Found by review, 2026-09-08, and it is the same defect `_set_busy` was
    written for in the first place -- the uninstall controls were simply added
    outside it. The state that loses data: tick "Keep my characters", press
    Uninstall, and while the 60-to-90-second teardown runs, untick the box and
    press again. `keep` is read at press time, so the second run resolves the
    plan afresh and removes `<project>_db-data` -- the volume the first press
    promised to keep.

    Asserted at both seams on purpose. The disabled widget is a statement about
    the button; the `_uninstall_running` guard is a statement about the action,
    and reaches the case where something else re-enables the widget or a
    queued click arrives anyway.
    """
    seen: list[bool] = []

    def while_running() -> None:
        seen.append(view.uninstall_confirm_button.isEnabled())
        seen.append(view.keep_characters_check.isEnabled())
        view.run_uninstall()  # the second press, mid-teardown

    view: ControllerView | None = None
    fake = _FakeUninstall(tmp_path, while_running=while_running)
    view = _uninstall_view(ps, tmp_path, fake)
    view.show_uninstall_plan()
    view.keep_characters_check.setChecked(True)
    view.run_uninstall()

    assert seen == [False, False], f"the uninstall controls stayed live: {seen}"
    assert len(fake.runs) == 1, f"the purge ran {len(fake.runs)} times, not once"
    assert fake.runs[0] is True, "the one run that happened did not keep the characters"


def test_a_ticked_plan_says_the_client_data_volume_is_kept_not_removed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Owner answer 3, 2026-09-08, read off the dialog before the press.

    A ticked purge keeps the 3.2 GB client-data volume as well as the database
    volume, and the sentence a person reads must say so -- the plan is what
    stands between them and a 3.2 GB re-download they did not expect.
    """
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    view.keep_characters_check.setChecked(True)
    view.show_uninstall_plan()

    said = view.uninstall_label.text()
    kept = [line for line in said.splitlines() if "KEPT" in line]
    removed = [line for line in said.splitlines() if "volumes removed" in line]
    assert kept and "_client-data" in kept[0], said
    assert removed and "_client-data" not in removed[0], said


def test_a_tab_with_no_uninstall_wired_shows_no_uninstall_controls(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.9a is WotLK; 8.9b is Vanilla. A tree without the seam offers no button."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.uninstall_button is None


# -- "Forget this install…", for a folder that is gone (T34) -------------------


def test_the_forget_button_is_hidden_while_the_folder_exists_and_appears_once_gone(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Re-checked on the same poll as the status line, not only at tab-build time.

    The owner's box had two stale tabs open with their folders already gone —
    the folder can disappear under a tab that has been open for a while, and
    the button has to notice without a restart.
    """
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    assert view.forget_install_button is not None
    view.refresh_status()
    assert view.forget_install_button.isHidden()

    shutil.rmtree(tmp_path)
    view.refresh_status()
    assert not view.forget_install_button.isHidden()
    # Mutation: negate `_update_forget_visibility()`'s `is_dir()` check (or
    # drop it) and this assertion is what catches it — the button would then
    # be shown for the folder that exists and hidden for the one that is gone.


def test_the_forget_button_stays_hidden_for_a_wsl_install_with_no_folder_here(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`server_dir.is_dir()` asks THIS process, which is right for a native
    install and wrong for one inside a WSL distro — the folder lives in the
    distro's own filesystem, not this one's. Out of scope for T34, and the
    button must not offer a wrong answer instead of no answer.
    """
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    view.services.controller.wsl_distro = "dml-arch"
    shutil.rmtree(tmp_path)
    view.refresh_status()
    assert view.forget_install_button is not None
    assert view.forget_install_button.isHidden()
    # Mutation: drop the `wsl_distro is None` clause from
    # `_update_forget_visibility()` and this fails — the button would show for
    # a distro path this process cannot evaluate.


def test_answering_yes_forgets_the_record_and_emits_uninstalled(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirm must compare with `==`, not `is` (T33's bug): this PySide6's
    static `QMessageBox.question()` returns a plain `int`, and the fake below
    returns exactly that — not the enum member — to prove the comparison
    survives it.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    shutil.rmtree(tmp_path)
    view.refresh_status()
    seen: list[tuple[str, object]] = []
    view.uninstalled.connect(lambda game, folder: seen.append((game, folder)))

    view.forget_install()

    assert fake.forgets == 1
    assert seen == [("wow-wotlk", tmp_path)]
    # Mutation: change `forget_install()`'s `answer == QMessageBox.StandardButton.Yes`
    # to `answer is QMessageBox.StandardButton.Yes` and this fails — the bare
    # `int` the fake returns is never `is` the enum member, so a real Yes reads
    # as a No and nothing is forgotten.


def test_answering_no_forgets_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_no_modal_dialogs` already answers No; this asserts what that means here."""
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.No),
    )
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    shutil.rmtree(tmp_path)
    view.refresh_status()
    seen: list[object] = []
    view.uninstalled.connect(lambda game, folder: seen.append(game))

    view.forget_install()

    assert fake.forgets == 0
    assert seen == []


def test_a_forget_that_raises_oserror_shows_the_error_and_keeps_the_tab(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`forget_record()`'s own docstring: `OSError` is not caught there because
    `purge.run()` catches it for the uninstall path — this is the OTHER caller
    of the same live-`AppState` seam, and it has to catch its own.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    fake = _FakeUninstall(tmp_path, forget_error=OSError("config dir is read-only"))
    view = _uninstall_view(ps, tmp_path, fake)
    shutil.rmtree(tmp_path)
    view.refresh_status()
    seen: list[object] = []
    failures: list[str] = []
    view.uninstalled.connect(lambda game, folder: seen.append(game))
    view.action_failed.connect(failures.append)

    view.forget_install()

    assert seen == [], "the tab was dropped over a forget that never happened"
    assert "config dir is read-only" in view.uninstall_label.text()
    assert failures and "config dir is read-only" in failures[0]


def test_the_folder_reappearing_before_the_press_forgets_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The visibility poll is not trusted at press time (review, T34 round 2).

    A restore, or simply undoing an accidental delete, can put the folder back
    in the gap between the five-second poll that showed the button and the
    click that reached it.
    """
    asked: list[object] = []
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: asked.append(1),
    )
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    shutil.rmtree(tmp_path)
    view.refresh_status()
    assert not view.forget_install_button.isHidden()
    tmp_path.mkdir()  # back before the press
    seen: list[object] = []
    view.uninstalled.connect(lambda game, folder: seen.append(game))

    view.forget_install()

    assert fake.forgets == 0
    assert seen == []
    assert asked == [], "the confirmation must not open for a folder that is back"
    assert view.uninstall_label.text() == f"{tmp_path} is back; nothing was forgotten."


def test_the_folder_reappearing_during_the_confirmation_forgets_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second re-check (review, T34 round 2): the gap between Yes and the write is real too.

    Mutation: drop the second `_forget_is_eligible()` call in
    `forget_install()` — the first call still sees the folder gone, so only
    the second one stands between this press and a forgotten record.
    """

    def question(*a: object, **k: object) -> object:
        tmp_path.mkdir()
        return int(controller_view_module.QMessageBox.StandardButton.Yes)

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    shutil.rmtree(tmp_path)
    view.refresh_status()
    seen: list[object] = []
    view.uninstalled.connect(lambda game, folder: seen.append(game))

    view.forget_install()

    assert fake.forgets == 0
    assert seen == []
    assert view.uninstall_label.text() == f"{tmp_path} is back; nothing was forgotten."


# -- the rebuild control (the action `_format_report` has always named) --------


def _rebuild_services(
    ps: _Ps, tmp_path: Path, lines: Sequence[str] = ("--- build", "done")
) -> tuple[ControllerServices, list[object]]:
    """Services whose rebuild seam records that it was asked, and what with.

    A list of the cancel events it was handed, so "was it started?" and "was it
    given a way to stop?" are two separate assertions rather than one flag.
    """
    started: list[object] = []
    services = _services(ps, tmp_path, [])

    def rebuild(cancel: object = None) -> Iterator[str]:
        started.append(cancel)
        yield from lines

    services.rebuild = rebuild
    return services, started


def test_the_modules_tab_offers_a_rebuild_beside_the_sentence_that_demands_one(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Until 2026-09-08 a grep for a rebuild button across `yulon/ui/` found NOTHING.

    `_format_report` has always printed "worldserver REBUILD required before
    this takes effect" — for 20 of the 41 shipped manifests, every one of them a
    `module` — naming an action the app did not have. The button lives on this
    tab because that is where the sentence is printed; a control the user has to
    go looking for on another tab is most of the way back to not having one.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert "ebuild" in view.rebuild_button.text()


def test_declining_the_rebuild_confirmation_starts_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half a warning label could not have: the user says no and nothing runs.

    `conftest._no_modal_dialogs` answers every `question()` with No, so this is
    also what every other test in this file asserts implicitly whenever it
    builds a view. Asserted three ways, because "the seam was not called" alone
    would be just as true of a button that is broken: the question really was
    asked, nothing was started, and the panel is not left claiming a job.
    """
    asked: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> object:
        asked.append(text)
        return controller_view_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    services, started = _rebuild_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.rebuild_server() is False
    assert started == [], "declined, and the rebuild ran anyway"
    assert asked, "the user was never asked"
    assert str(tmp_path) in asked[0], "the question did not say which install it is about"
    assert view.rebuild_log.running is False


def test_accepting_the_rebuild_confirmation_streams_the_engine_into_the_panel(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 5: what a person watches is a panel with the engine's own lines in it.

    A `QPlainTextEdit` that says "rebuilding…" and then nothing for an hour is
    indistinguishable from a hang, which is the state this app has already put a
    user in once. `LogPanel` is the widget that solves it — timestamped lines, a
    ticking elapsed field beside a Stop button — and it is reused rather than
    respelled.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    services, started = _rebuild_services(ps, tmp_path, lines=("--- build", "compiling"))
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.rebuild_server() is True
    # The panel's lines cross from its worker thread by a QUEUED connection, so
    # the thread ending is not the same as the text having arrived; `pump_until`
    # is what makes the difference, and it reports an expiry rather than
    # returning silently on the deadline.
    pump_until(
        lambda: "compiling" in view.rebuild_log.text(),
        "the rebuild's output reached the panel",
    )
    assert len(started) == 1, started
    assert started[0] is not None, "the panel's Stop button has nothing to set"
    text = view.rebuild_log.text()
    assert "--- build" in text and "compiling" in text, text


def test_a_real_static_ints_yes_still_starts_the_rebuild(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T33's other shape at this site: PySide6's real static `question()` return, not the enum.

    The test above answers with the `QMessageBox.StandardButton` member, which
    is exactly the shape that hid the bug: PySide6 6.11.2's static
    `QMessageBox.question()` returns the plain `int` used here instead
    (`.notes/gates/t33-yes-reads-as-no-2026-09-11/static_probe.py`).
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    services, started = _rebuild_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.rebuild_server() is True
    # `started` crosses from the panel's worker thread, so the handler returning
    # is not the press having actually run yet — `pump_until` is what makes the
    # difference (`test_accepting_the_rebuild_confirmation_streams_the_engine_into_the_panel`
    # says so in the same words).
    pump_until(lambda: "done" in view.rebuild_log.text(), "the rebuild's output reached the panel")
    assert len(started) == 1, "an int Yes from the static question() did not start the rebuild"


def test_the_rebuild_confirmation_offers_yes_and_no_and_defaults_to_refusing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirm whose default is Yes is a notice with extra steps.

    Neither the buttons nor the default is observable from the outcome, so this
    is the one place the call's ARGUMENTS are the subject. An hour of compiling
    and a server going down is not something Enter should be able to start.
    """
    calls: list[tuple[object, ...]] = []

    def question(*a: object, **k: object) -> object:
        calls.append(a)
        return controller_view_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    services, _ = _rebuild_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view.rebuild_server()

    buttons, default = calls[0][-2], calls[0][-1]
    yes = controller_view_module.QMessageBox.StandardButton.Yes
    no = controller_view_module.QMessageBox.StandardButton.No
    assert buttons == yes | no
    assert default is no, "Enter would start an hour of compiling"


def test_a_game_with_no_rebuild_wiring_greys_the_button_instead_of_failing_on_click(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`services.rebuild` is None when nothing wired one; the tab says so up front.

    The same rule the console tab applies to a missing pty and the catalog tile
    to an unsupported platform (roadmap 6.1): refusing on click and printing the
    error afterwards is not the same as saying so before it is pressed.
    """
    services = _services(ps, tmp_path, [])
    services.rebuild = None
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert view.rebuild_button.isEnabled() is False
    assert view.rebuild_server() is False


def test_the_rebuild_panel_is_joined_at_shutdown_like_every_other_worker(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A `QThread` destroyed while running ABORTS the process (0xC0000409, verified).

    Every LogPanel this view owns has to be reachable from the exit path, and
    the view grew a second one with this feature. `log_panels()` is what
    `main.py` walks, so a third panel added later is registered by existing
    code rather than by remembering to edit two files.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert set(view.log_panels()) == {view.console_log, view.rebuild_log}
    view.shutdown()
    assert view.rebuild_log.running is False


def test_a_server_adopted_from_wsl_is_refused_a_rebuild_by_name(tmp_path: Path) -> None:
    """The wiring's own refusal, and the one this app is least able to notice going wrong.

    `native.Seams` addresses the LOCAL daemon: four of its five 7.3 primitives
    take a `wsl_distro` the field types do not carry, and its own docstring
    records that a repair reaching a stage on an adopted install "would hand
    these seams a container living on another daemon, and the erasure would then
    send all of them to the wrong one silently". A rebuild is exactly that
    repair. So it is refused in the wiring, where the distro is known, rather
    than left to build images on the Windows-local daemon and then fail to find
    containers that live inside the distro.
    """
    services = ControllerServices.for_entry(WOTLK, tmp_path, None, "Ubuntu-22.04")
    assert services.rebuild is not None
    with pytest.raises(InstallerError) as raised:
        list(services.rebuild(None))
    message = str(raised.value)
    assert "Ubuntu-22.04" in message
    assert "Nothing was started" in message


def test_a_local_install_gets_a_rebuild_seam_on_every_game(tmp_path: Path) -> None:
    """Every tab this app can open offers the control, not just the one with modules.

    A CMaNGOS install has no manifest store and so never prints the REBUILD
    sentence, but its worldserver is compiled from the same kind of checkout and
    its users patch it the same way. Wiring the seam in `_assemble()` — the
    shared half — is what makes that true by construction rather than by
    remembering it four times.
    """
    for entry in _every_game():
        services = ControllerServices.for_entry(entry, tmp_path / entry.id)
        assert services.rebuild is not None, entry.id


def test_the_rebuild_sentence_names_a_button_that_is_really_on_the_tab(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The sentence and the control, asserted against each other rather than separately.

    "worldserver REBUILD required before this takes effect" was printed for 20
    of the 41 shipped manifests while a grep for a rebuild button across
    `yulon/ui/` returned nothing. Two tests — one that the sentence appears and
    one that a button exists — would both have been green with the sentence
    pointing at a control on another tab, or at one renamed since. This reads
    the label off the widget and looks for it in the report the user is shown.
    """
    # The asker is injected for the reason `test_modules_tab_lists_manifests_and
    # _installs_selected` injects it: `mod-ah-bot` is one of the two manifests that
    # HAS a question, and with the real one this test sits on a modal dialog for
    # ever. It was written on a branch whose base predates that seam, and this is
    # what it did when it was first run on a tree that has it.
    view = ControllerView(
        WOTLK,
        _services(ps, tmp_path, []),
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts: {p.key: "42" for p in prompts},
    )
    view.modules_panel.select("mod-ah-bot")
    view._module_action("install")

    report = view.module_report.toPlainText()
    assert "REBUILD required" in report
    assert view.rebuild_button.text() in report, (
        "the report tells the user to press something whose name is not on this tab: " f"{report!r}"
    )


def test_a_running_rebuild_locks_the_server_tab_and_unlocks_it_afterwards(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start, Stop and Remove act on the containers a rebuild is replacing.

    Both directions, because either alone is a hole: a Stop pressed mid-rebuild
    fights the recreate, and a Rebuild pressed during the 10-30 minute import
    (which `busy_reason()` records cannot be stopped at all) tears down the
    database it is writing into. The unlock is asserted after a job that FAILS,
    which is the shape a hand-placed `_set_busy(False)` misses.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: controller_view_module.QMessageBox.StandardButton.Yes,
    )

    def refuses(cancel: object = None) -> Iterator[str]:
        yield "starting"
        raise InstallerError("that folder has no install record")

    services = _services(ps, tmp_path, [])
    services.rebuild = refuses
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    assert view.start_button.isEnabled()
    assert view.rebuild_server() is True
    pump_until(lambda: bool(failures), "the failed rebuild reported itself")

    assert view.start_button.isEnabled() is False
    assert view.stop_button.isEnabled() is False
    assert "no install record" in failures[0], failures
    # The panel's own header is one wrapped label; the refusal also has to reach
    # the channel `main.py` writes to the app log.
    assert "FAILED" in view.rebuild_log.status_text()

    # And the lock comes off — including for the buttons `_set_busy` re-enables
    # rather than the ones it left alone.
    view.refresh_status()
    assert view.rebuild_button.isEnabled() is True
    assert view.repair_button.isEnabled() is True


def test_a_rebuild_is_refused_while_another_action_of_this_tab_is_running(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror of the lock above, and the one that protects a running import."""
    answered: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> object:
        answered.append(title)
        return controller_view_module.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    services, started = _rebuild_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view._set_busy(True)

    assert view.rebuild_server() is False
    assert started == [], "a rebuild started on top of another action"
    assert answered == [], "the confirmation was shown for a press that could not run"
    assert view.rebuild_button.isEnabled() is False


def test_a_job_ending_never_hands_back_a_button_the_game_cannot_use(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Unlocking must not re-enable a control a standing fact disabled.

    Exactly the bug `catalog_view._set_buttons_enabled()` carries its own
    paragraph about: that pass knows only whether a job is running, while
    "this game has no rebuild wiring" is a fact about the TAB. A blanket
    `setEnabled(True)` on the way out survives every other test in this file —
    measured as a surviving mutation on 2026-09-08 — because nothing else ever
    unlocks a tab whose rebuild is None.
    """
    services = _services(ps, tmp_path, [])
    services.rebuild = None
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert view.rebuild_button.isEnabled() is False

    view._set_busy(True)
    view._set_busy(False)

    assert (
        view.rebuild_button.isEnabled() is False
    ), "a job ending handed back a button whose action does not exist"


# ------------------- the guard, on the button (8.7a / T7)


_DIRECT_WORLD_MOD: dict[str, object] = {
    "schema_version": 1,
    "id": "world-sql",
    "name": "World SQL",
    "type": "mod",
    "game": "wow-wotlk",
    "build": {"rebuild": False, "restart": True},
    "sql": [{"db": "world", "statement": "UPDATE item_template SET stackable = 200"}],
}


def _no_sql_reaches_the_database(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record what the tab's REAL `DockerSql` was asked to run, without replacing it.

    The applier under test is the one `for_entry()` built, holding the runner it
    built -- nothing here reaches inside it. The two `SqlRunner` methods are
    recorded on the class instead, which is the seam the engine calls, so a
    statement that got past the guard is counted rather than shelled out.
    """
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        DockerSql, "run_statement", lambda self, db, statement: sent.append((db, statement))
    )
    monkeypatch.setattr(DockerSql, "run_file", lambda self, db, path: sent.append((db, path.name)))
    return sent


def test_the_wotlk_modules_tab_refuses_direct_world_sql_while_the_world_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """8.7a's second clause, on the applier `for_entry()` really hands the tab.

    This is the test T2's press could not be: that gate wired `world_running`
    onto the applier itself, because no shipped caller passed one, and its
    README leads with *"met by the engine and by no button"*. Here nothing is
    attached and no private field is touched -- `docker.world_running` is
    patched, which is the function the view's own lambda calls, and the refusal
    has to travel the whole shipped path to arrive.

    Catches the four view sites rewired to anything but `docker.world_running`
    (a patched function nobody calls changes nothing, and the real
    `container_state` would shell out to the docker CLI, which `conftest`'s
    guard fails on), the seam dropped between factory and `Applier`, and the
    guard itself deleted.
    """
    server_dir = tmp_path / "wotlk"
    server_dir.mkdir()
    (server_dir / (WOTLK.install.password.file or ".db_password")).write_text(
        "hunter2", encoding="utf-8"
    )
    asked: list[str] = []

    def world_running(container: str, *, wsl_distro: str | None = None) -> bool | None:
        asked.append(container)
        return True

    monkeypatch.setattr(docker, "world_running", world_running)
    sent = _no_sql_reaches_the_database(monkeypatch)

    services = ControllerServices.for_entry(WOTLK, server_dir)
    assert services.applier is not None

    with pytest.raises(apply_module.ApplyError) as raised:
        services.applier.install(parse_manifest(_DIRECT_WORLD_MOD))

    assert "the world server is running" in str(raised.value)
    assert "sql inline → world" in str(raised.value)
    assert sent == [], "the guard is a pre-pass: nothing reached the runner"
    assert asked == [WOTLK.container_spec().world], "asked about THIS install's world container"


def test_the_wotlk_modules_tab_refuses_when_it_cannot_tell_whether_the_world_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `None` branch, on the button -- the one T2's press never saw live.

    `docker.container_state()` answers an empty `ContainerState` for a missing
    container and for a daemon that will not reply, and `.settled` turns that
    into `False`, which through this guard is fail-OPEN. The view calls
    `docker.world_running()` precisely so that arrives as `None`.

    Catches a view site rewired to `container_state(...).settled` or to
    `status == "running"`: both answer `False` here, the install would proceed,
    and this test would find the statement sitting in `sent`.
    """
    server_dir = tmp_path / "wotlk"
    server_dir.mkdir()
    (server_dir / (WOTLK.install.password.file or ".db_password")).write_text(
        "hunter2", encoding="utf-8"
    )
    monkeypatch.setattr(docker, "world_running", lambda container, wsl_distro=None: None)
    sent = _no_sql_reaches_the_database(monkeypatch)

    services = ControllerServices.for_entry(WOTLK, server_dir)
    assert services.applier is not None

    with pytest.raises(apply_module.ApplyError) as raised:
        services.applier.install(parse_manifest(_DIRECT_WORLD_MOD))

    assert "could not tell whether the world server is running" in str(raised.value)
    assert sent == []


def test_a_paused_world_refuses_direct_sql_on_the_wotlk_modules_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T20 (Codex, on `a6e2aff6`): `paused` used to read as down through this guard.

    `docker.container_state` is patched here, not `docker.world_running` as
    the two tests above patch it -- this one has to travel THROUGH
    `docker.world_running`'s own `paused`-to-`True` mapping, not around it, or
    it would prove nothing about the table the finding was about. The view
    still calls `docker.world_running()`, which still calls
    `docker.container_state()`, so a real `docker inspect` reporting `paused`
    reaches the guard exactly as it would on a live box.

    Its mutation is the old table, `paused: False`: with that in place a
    paused world reads as not running, the guard returns instead of refusing,
    and the statement lands in `sent`.
    """
    server_dir = tmp_path / "wotlk"
    server_dir.mkdir()
    (server_dir / (WOTLK.install.password.file or ".db_password")).write_text(
        "hunter2", encoding="utf-8"
    )
    asked: list[str] = []

    def container_state(container: str, *, wsl_distro: str | None = None) -> docker.ContainerState:
        asked.append(container)
        return docker.ContainerState("paused", "T", 0)

    monkeypatch.setattr(docker, "container_state", container_state)
    sent = _no_sql_reaches_the_database(monkeypatch)

    services = ControllerServices.for_entry(WOTLK, server_dir)
    assert services.applier is not None

    with pytest.raises(apply_module.ApplyError) as raised:
        services.applier.install(parse_manifest(_DIRECT_WORLD_MOD))

    assert "the world server is running" in str(raised.value)
    assert "sql inline → world" in str(raised.value)
    assert sent == [], "the guard is a pre-pass: nothing reached the runner"
    assert asked == [WOTLK.container_spec().world], "asked about THIS install's world container"


# ------------------------------------ the pending-database-updates button (T14)
#
# T11 built the route that applies a phase declared `rerun_on_marked` to an
# install the probe already reads as finished, and its reviewer then found the
# route had no way in from the app: the catalog tile greys to "Installed" once
# the folder is known, and `rebuild_stages()` excludes `import` on purpose. The
# tests below are about the button that closes that — who is offered it, and the
# refusal that has to arrive through the shipped wiring rather than through a
# seam a test attached.


def _updates_services(
    ps: _Ps, tmp_path: Path, lines: Sequence[str] = ("--- import", "applied")
) -> tuple[ControllerServices, list[object], list[str]]:
    """Services whose updates route records the confirmation it gave and the press it took."""
    started: list[object] = []
    asked: list[str] = []
    services = _services(ps, tmp_path, [])

    def confirmation() -> str:
        asked.append("confirmation")
        return "apply three files?"

    def press(cancel: object = None) -> Iterator[str]:
        started.append(cancel)
        yield from lines

    services.updates = native.UpdateRoute(confirmation=confirmation, press=press)
    return services, started, asked


def _entry_declaring_a_rerunnable_phase(game: str = "wow-tortoise") -> CatalogEntry:
    """The shipped entry with one file phase flagged `rerun_on_marked`, and nothing else changed.

    Until T30 the shipped `wow-tortoise` plan carried such a phase --
    `character updates`, the retired fork's own `sql/character_updates/`, which
    nothing on that tree applied -- and the presses below read it straight off
    the catalog. The Penqle core has no such directory, so the plan has no such
    phase and NO SHIPPED ENTRY DECLARES ONE. That is the rule these presses
    follow, not an exception to it: `services.updates` and `services.adopt` are
    `None` for every game today, which is what
    `test_the_updates_button_is_offered_only_where_the_plan_declares_a_rerunnable_phase`
    now asserts.

    The presses themselves still have to work the day a flagged phase comes
    back, and they are the route that writes DDL into somebody's live
    characters database, so they go on being exercised -- against an entry that
    declares one. Built from the shipped entry by round-tripping it through the
    model so it is the real wiring, the real plan and the real guards, differing
    in exactly the one flag.
    """
    entry = load_catalog().get(game)
    data = entry.model_dump(mode="json")
    phases = data["install"]["native"]["cmangos"]["sql"]["phases"]
    flagged = next(phase for phase in phases if phase["name"] == "world base")
    flagged["rerun_on_marked"] = True
    return CatalogEntry.model_validate(data)


def test_the_updates_button_is_offered_only_where_the_plan_declares_a_rerunnable_phase(
    tmp_path: Path,
) -> None:
    """The enabling rule at the wiring, over every game the app can manage.

    Read off the catalog by `native.update_phases()`, so this is the same
    question `test_database_updates.py` asks of the data, asked here of what
    `for_entry()` actually hands a tab. No shipped entry declares such a phase
    since T30 -- Tortoise was the one that did, and the directory it applied is
    not on the Penqle core -- so all four get `None` and a dead control, which
    is the rule the rebuild seam already follows: a control that is visibly
    unavailable beats one that is pressed and then explains itself.

    Both directions, because an "everything is None" assertion alone would be
    satisfied by a route that is simply broken. The entry that DOES declare one
    is built beside it from the shipped data.

    Catches the route wired for every entry (four games would then offer a
    press that applies nothing and reports success), the reader hard-coded to
    an id, and the route removed with the phase.
    """
    for game in ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise"):
        entry = load_catalog().get(game)
        assert ControllerServices.for_entry(entry, tmp_path / game).updates is None, game
    flagged = _entry_declaring_a_rerunnable_phase()
    assert ControllerServices.for_entry(flagged, tmp_path / "flagged").updates is not None


def test_a_server_adopted_from_a_wsl_distro_is_not_offered_the_updates_button(
    tmp_path: Path,
) -> None:
    """The same refusal `rebuild_for_app()` exists for, taken as a greying rather than a sentence.

    `native.Seams` addresses the local daemon and erases `wsl_distro`, so a
    press against a server living inside a distro would ask THIS Docker about a
    container it has never heard of. That answer is `None` and the guard refuses
    on it, which is safe but says the wrong thing; withholding the control says
    the true one.

    Catches the `wsl_distro` test dropped from the wiring.
    """
    tortoise = _entry_declaring_a_rerunnable_phase()
    inside = ControllerServices.for_entry(tortoise, tmp_path / "tw", wsl_distro="Ubuntu")
    assert inside.updates is None


def test_a_tab_with_no_updates_route_has_a_dead_button_that_is_harmless_to_press(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`services.updates is None` greys it, and pressing it anyway does nothing.

    Catches the button enabled unconditionally, and `apply_database_updates()`
    reaching for a route it was never given.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert not view.updates_button.isEnabled()
    assert view.apply_database_updates() is False


def test_declining_the_updates_confirmation_starts_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is real: the question is asked, and No means nothing ran.

    `said_yes(...)` rather than a check for No, because Escape and the window's
    close button both answer `NoButton` — and this press writes DDL into a
    database with somebody's characters in it.

    Catches the confirmation skipped, and the verdict read as `== No`.
    """
    seen: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> object:
        seen.append(text)
        return controller_view_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    services, started, asked = _updates_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.updates_button.isEnabled()
    assert view.apply_database_updates() is False
    assert seen == ["apply three files?"], seen
    assert asked == ["confirmation"], "the engine's own confirmation text was not used"
    assert started == [], "declined, and the press ran anyway"
    assert view.rebuild_log.running is False


def test_a_confirmation_that_refuses_puts_the_sentence_where_the_user_is_and_starts_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone that predates the directory these phases name refuses at `expand()`.

    That refusal arrives while the dialog is being COMPOSED — the file list is
    expanded from the folder, so there is no list to offer — and it must not
    become a traceback, an empty dialog, or a press. T11's reviewer, note 4.

    Catches the confirmation call left outside a `try`, and a refusal that
    yields an empty file list instead of raising.
    """
    asked: list[str] = []

    def question(*a: object, **k: object) -> object:
        asked.append("asked")
        return controller_view_module.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    services, started, _ = _updates_services(ps, tmp_path)
    route = services.updates
    assert route is not None

    def refuse() -> str:
        raise InstallerError("found no file matching src/x/*.sql under /srv")

    services.updates = native.UpdateRoute(confirmation=refuse, press=route.press)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    assert view.apply_database_updates() is False
    assert started == [], "refused, and the press ran anyway"
    assert asked == [], "the user was asked to confirm a press that could not be described"
    assert failures and "found no file matching" in failures[0], failures


def test_a_real_static_ints_yes_still_starts_the_database_updates(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T33's other shape at this site: PySide6's real static `question()` return, not the enum.

    Every other test of this confirm answers with the `QMessageBox.StandardButton`
    member, which is exactly the shape that hid the bug: PySide6 6.11.2's static
    `QMessageBox.question()` returns the plain `int` used here instead
    (`.notes/gates/t33-yes-reads-as-no-2026-09-11/static_probe.py`).
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    services, started, asked = _updates_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.apply_database_updates() is True
    assert asked == ["confirmation"], "the engine's own confirmation text was not used"
    # `started` crosses from the panel's worker thread; see the rebuild version
    # of this test for why the wait is needed before it can be read.
    pump_until(
        lambda: "applied" in view.rebuild_log.text(), "the update's output reached the panel"
    )
    assert len(started) == 1, "an int Yes from the static question() did not start the press"


def test_the_tortoise_updates_button_refuses_while_the_world_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal on the route `for_entry()` really builds, with nothing attached by hand.

    `docker.world_running` is patched — the function the engine's own seam
    resolves on the call — so the refusal has to travel the whole shipped path
    to arrive: the tab's wiring, `install_wiring.installer_for_app()`, and
    `update_databases()`'s guard. T11's reviewer (note 3) recorded that this
    route writes DDL into `tw_char` and that nothing stopped the world; this is
    that, refused before the database is even started.

    Catches the guard deleted, the seam bound to `container_state(...).settled`
    (which answers `False` here and would let the press through), and a wiring
    that hands the engine a `world_running` bound at import.
    """
    tortoise = _entry_declaring_a_rerunnable_phase()
    server_dir = tmp_path / "tw"
    server_dir.mkdir()
    asked: list[str] = []

    def world_running(container: str, *, wsl_distro: str | None = None) -> bool | None:
        asked.append(container)
        return True

    monkeypatch.setattr(docker, "world_running", world_running)
    services = ControllerServices.for_entry(tortoise, server_dir)
    assert services.updates is not None

    with pytest.raises(InstallerError) as raised:
        list(services.updates.press(None))

    assert "world server is running" in str(raised.value)
    assert "Press Stop" in str(raised.value)
    assert asked == [tortoise.container_spec().world], "asked about THIS install's world container"


def test_the_tortoise_updates_button_refuses_when_it_cannot_tell_whether_the_world_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`None` is not "no", on the button.

    An unreadable inspect and a missing container both answer `None`, and
    through this guard `False` would be fail-OPEN. Nothing reaches the daemon:
    `conftest`'s guard fails any test whose argv gets to the docker CLI, so a
    press that got past this refusal would be red here for a second reason.

    Catches the `None` branch folded into the `False` one.
    """
    tortoise = _entry_declaring_a_rerunnable_phase()
    server_dir = tmp_path / "tw"
    server_dir.mkdir()
    monkeypatch.setattr(docker, "world_running", lambda container, wsl_distro=None: None)
    services = ControllerServices.for_entry(tortoise, server_dir)
    assert services.updates is not None

    with pytest.raises(InstallerError) as raised:
        list(services.updates.press(None))

    assert "could not tell whether" in str(raised.value)


# ---------------------------------------------- the adopt-as-imported button (T19)
#
# T14's button refuses an install with no marker row, and the install it was
# built for is exactly that. The probe cannot prove such an import finished --
# three rounds tried -- so this button asks the person instead. What the tests
# below are about is the greying, because that is where this control differs
# from every other one on the tab: it is offered on a READING of the databases
# and not on a fact about the catalog, and the reading is taken once per time
# the database comes up rather than on every poll or every paint.

POPULATED = docker.ImportState("populated", "903 rows in tw_char.characters")
ADOPT_ANSWERS = {
    "populated": POPULATED,
    "imported": docker.ImportState("imported", "tw_world.yulon_install records it", complete=True),
    "absent": docker.ImportState("absent", "no schema exists yet"),
    "partial": docker.ImportState("partial", "tw_char exists, no marker"),
    "unreadable": docker.ImportState("unreadable", "the databases would not answer"),
}


def _adopt_services(
    ps: _Ps,
    tmp_path: Path,
    answer: docker.ImportState = POPULATED,
    lines: Sequence[str] = ("--- adopt", "the row is written"),
) -> tuple[ControllerServices, list[object], list[str], list[str]]:
    """Services whose adopt route records every question put to it and every press taken."""
    started: list[object] = []
    asked: list[str] = []
    probed: list[str] = []
    services = _services(ps, tmp_path, [])

    def state() -> docker.ImportState:
        probed.append("state")
        return answer

    def confirmation() -> str:
        asked.append("confirmation")
        return "write one row?"

    def press(cancel: object = None) -> Iterator[str]:
        started.append(cancel)
        yield from lines

    services.adopt = native.AdoptRoute(state=state, confirmation=confirmation, press=press)
    return services, started, asked, probed


@pytest.mark.parametrize("named", sorted(ADOPT_ANSWERS))
def test_the_adopt_button_is_live_for_populated_databases_and_no_others(
    qapp: object, ps: _Ps, tmp_path: Path, named: str
) -> None:
    """The enabling rule, one test per answer the probe can give.

    `populated` and nothing else. `imported` means the row is already there and
    the press would refuse; `absent` and `partial` mean there is no import to
    make a claim about; `unreadable` means nobody could look -- including the
    ordinary case where the database is simply down, which must never arm a
    control that writes a marker row.

    Enumerated rather than sampled because the danger is per answer: a rule
    written as `!= "imported"` passes a sampled test and arms this press on a
    database that answered nothing.

    Catches the rule widened to "not imported", written as truthiness of the
    reading (every answer is truthy), or dropped so the button follows
    `services.adopt` alone.
    """
    services, _, _, probed = _adopt_services(ps, tmp_path, ADOPT_ANSWERS[named])
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert not view.adopt_button.isEnabled(), "nothing has been asked yet"
    ps.names = "ac-database\n"
    view.refresh_status()
    assert probed == ["state"], probed
    assert view.adopt_button.isEnabled() is (named == "populated")


def test_the_adopt_reading_is_taken_once_per_time_the_database_comes_up(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Not on every poll, and not on every paint. The probe costs `docker exec`s.

    The five-second poll runs forever on every tab the app has open, and this
    reading is `MarkerGate.probe()` — several `docker exec … mariadb` calls
    against the install's database. Taken on the poll it would be several of
    those every five seconds; taken at tab build time it would be paid for by
    every install that opens a controller view, most of which will never press
    this. Once per time the database comes up is the rule the import question
    beside it already keeps.

    And it is DROPPED when the database goes down, rather than kept: the answer
    was taken from a database nothing can now renew it against, and a control
    that writes a marker row must not stay lit on one.

    Catches the reading taken in `_status_ready` unguarded, taken in
    `_build_modules_tab`, and remembered across a database that went away.
    """
    services, _, _, probed = _adopt_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    ps.names = "ac-database\n"
    view.refresh_status()
    view.refresh_status()
    view.refresh_status()
    assert probed == ["state"], probed
    assert view.adopt_button.isEnabled()

    ps.names = ""
    view.refresh_status()
    assert not view.adopt_button.isEnabled(), "the database went away and the reading with it"
    ps.names = "ac-database\n"
    view.refresh_status()
    assert probed == ["state", "state"], probed


def test_a_probe_that_raises_leaves_the_adopt_button_dead(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The one outcome that must never follow from a question nobody answered.

    `AdoptRoute.state` is documented not to raise; this is the boundary that
    holds if some future gate forgets, and what it would otherwise arm is a
    press that writes a completion marker on a database that could not be read.

    Catches the failure slot left off the `_run` call, and a slot that keeps the
    last good reading.
    """
    services, _, _, _ = _adopt_services(ps, tmp_path)

    def angry() -> docker.ImportState:
        raise RuntimeError("the daemon is not there")

    route = services.adopt
    assert route is not None
    services.adopt = native.AdoptRoute(
        state=angry, confirmation=route.confirmation, press=route.press
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    ps.names = "ac-database\n"
    view.refresh_status()
    assert not view.adopt_button.isEnabled()


def test_a_tab_with_no_adopt_route_has_a_dead_button_that_is_harmless_to_press(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`services.adopt is None` greys it, and pressing it anyway does nothing.

    Catches the button enabled unconditionally, and `adopt_as_imported()`
    reaching for a route it was never given.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert not view.adopt_button.isEnabled()
    assert view.adopt_as_imported() is False


def test_the_adopt_button_is_offered_only_where_the_plan_declares_a_rerunnable_phase(
    tmp_path: Path,
) -> None:
    """The catalog half of the greying, over every game the app can manage.

    Adopting buys nothing where no later press would then do anything it cannot
    do now, and the row it writes is a claim nothing takes back — so it is
    refused there rather than allowed as harmless.

    Catches the route wired for every entry, and the reader hard-coded to an id.
    """
    for game in ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise"):
        entry = load_catalog().get(game)
        assert ControllerServices.for_entry(entry, tmp_path / game).adopt is None, game
    flagged = _entry_declaring_a_rerunnable_phase()
    assert ControllerServices.for_entry(flagged, tmp_path / "flagged").adopt is not None


def test_a_server_adopted_from_a_wsl_distro_is_not_offered_the_adopt_button(
    tmp_path: Path,
) -> None:
    """`native.Seams` addresses the local daemon and erases `wsl_distro`.

    A press against a server living inside a distro would ask THIS Docker about
    a container it has never heard of; withholding the control says the true
    thing where refusing would say the wrong one.

    Catches the `wsl_distro` test dropped from the wiring.
    """
    tortoise = _entry_declaring_a_rerunnable_phase()
    inside = ControllerServices.for_entry(tortoise, tmp_path / "tw", wsl_distro="Ubuntu")
    assert inside.adopt is None


def test_declining_the_adopt_confirmation_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is real, and this is the press where it matters most.

    `said_yes(...)` rather than a check for No, because Escape and the window's
    close button both answer `NoButton` — and Yes here is a person saying
    something about their databases that nothing takes back.

    Catches the confirmation skipped, and the verdict read as `== No`.
    """
    seen: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> object:
        seen.append(text)
        return controller_view_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    services, started, asked, _ = _adopt_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    ps.names = "ac-database\n"
    view.refresh_status()

    assert view.adopt_button.isEnabled()
    assert view.adopt_as_imported() is False
    assert seen == ["write one row?"], seen
    assert asked == ["confirmation"], "the engine's own confirmation text was not used"
    assert started == [], "declined, and the press ran anyway"
    assert view.rebuild_log.running is False


def test_the_adopt_press_runs_the_routes_own_generator_into_the_panel(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yes starts the engine's own press, with the panel's Stop wired to its cancel.

    Catches the handler running something other than `route.press`, and a press
    started without an event the panel's Stop can set.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    services, started, _, _ = _adopt_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    ps.names = "ac-database\n"
    view.refresh_status()
    assert view.adopt_as_imported() is True
    # The press runs on the panel's worker thread and its lines cross back by a
    # queued connection, so the handler returning is not the press having run;
    # `pump_until` is what makes the difference (the rebuild's own tests say so
    # in the same words).
    pump_until(
        lambda: "the row is written" in view.rebuild_log.text(),
        "the adopt press's output reached the panel",
    )
    assert len(started) == 1, started
    assert started[0] is not None, "the panel's Stop button has nothing to set"
    assert "--- adopt" in view.rebuild_log.text()


def test_a_real_static_ints_yes_still_starts_the_adopt_press(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T33's other shape at this site: PySide6's real static `question()` return, not the enum.

    The test above answers with the `QMessageBox.StandardButton` member, which
    is exactly the shape that hid the bug: PySide6 6.11.2's static
    `QMessageBox.question()` returns the plain `int` used here instead
    (`.notes/gates/t33-yes-reads-as-no-2026-09-11/static_probe.py`).
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    services, started, _, _ = _adopt_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    ps.names = "ac-database\n"
    view.refresh_status()

    assert view.adopt_as_imported() is True
    # `started` crosses from the panel's worker thread; see the rebuild version
    # of this test for why the wait is needed before it can be read.
    pump_until(
        lambda: "the row is written" in view.rebuild_log.text(),
        "the adopt press's output reached the panel",
    )
    assert len(started) == 1, "an int Yes from the static question() did not start the press"


def test_the_adopt_button_greys_itself_once_its_own_press_has_finished(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The press changes the answer, so the answer is asked again.

    A button still lit after the row it writes has been written would offer a
    press whose only outcome is the refusal "these databases already carry
    Yu'lon's marker". The reading is dropped when any job on this tab finishes
    and re-taken on the next poll — which is also why a rebuild or an updates
    press drops it: both can change what the databases read as.

    Catches the reading kept across a finished job.
    """
    services, _, _, probed = _adopt_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    ps.names = "ac-database\n"
    view.refresh_status()
    assert view.adopt_button.isEnabled()

    view._rebuild_finished(True, "")
    assert not view.adopt_button.isEnabled(), "the reading survived the press that changed it"
    view.refresh_status()
    assert probed == ["state", "state"], probed


def test_the_tortoise_adopt_press_refuses_while_the_world_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal on the route `for_entry()` really builds, with nothing attached by hand.

    `docker.world_running` is patched — the function the engine's own seam
    resolves on the call — so the refusal has to travel the whole shipped path
    to arrive: the tab's wiring, `install_wiring.installer_for_app()` and
    `adopt_as_imported()`'s guard. And the sentence has to name THIS button: a
    refusal from the adopt press telling the user to press "Apply pending
    database updates…" again is an instruction that does the wrong thing when
    followed.

    Catches the guard deleted, the seam bound to `container_state(...).settled`
    (which answers `False` here and would let the press through), and the
    guard's button label left hard-coded to the updates one.
    """
    tortoise = _entry_declaring_a_rerunnable_phase()
    server_dir = tmp_path / "tw"
    server_dir.mkdir()
    asked: list[str] = []

    def world_running(container: str, *, wsl_distro: str | None = None) -> bool | None:
        asked.append(container)
        return True

    monkeypatch.setattr(docker, "world_running", world_running)
    services = ControllerServices.for_entry(tortoise, server_dir)
    assert services.adopt is not None

    with pytest.raises(InstallerError) as raised:
        list(services.adopt.press(None))

    assert "world server is running" in str(raised.value)
    assert native.ADOPT_BUTTON_LABEL in str(raised.value)
    assert native.UPDATES_BUTTON_LABEL not in str(raised.value)
    assert asked == [tortoise.container_spec().world], "asked about THIS install's world container"


def test_the_tortoise_adopt_press_refuses_when_it_cannot_tell_whether_the_world_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`None` is not "no", on this button too, and the remedy names Docker.

    Nothing reaches the daemon: `conftest`'s guard fails any test whose argv
    gets to the docker CLI, so a press that got past this refusal would be red
    here for a second reason.

    Catches the `None` branch folded into the `False` one.
    """
    tortoise = _entry_declaring_a_rerunnable_phase()
    server_dir = tmp_path / "tw"
    server_dir.mkdir()
    monkeypatch.setattr(docker, "world_running", lambda container, wsl_distro=None: None)
    services = ControllerServices.for_entry(tortoise, server_dir)
    assert services.adopt is not None

    with pytest.raises(InstallerError) as raised:
        list(services.adopt.press(None))

    assert "could not tell whether" in str(raised.value)
    assert "check that Docker is running" in str(raised.value)


def test_the_tortoise_adopt_confirmation_names_the_row_through_the_shipped_wiring(
    tmp_path: Path,
) -> None:
    """The dialog a user really meets, composed through `for_entry()` and nothing else.

    It asks the databases nothing — every reading this press makes is in the
    press — so it can be composed here with no daemon at all, which is also why
    a clone this app has never touched still gets a truthful dialog.

    Catches the confirmation reaching for a database, and a row named from
    anything but the plan the writer reads.
    """
    tortoise = _entry_declaring_a_rerunnable_phase()
    server_dir = tmp_path / "tw"
    server_dir.mkdir()
    services = ControllerServices.for_entry(tortoise, server_dir)
    assert services.adopt is not None
    said = services.adopt.confirmation()
    assert native.ADOPT_CONSEQUENCE in said
    assert sqlplan.MARKER_TABLE in said
    assert str(server_dir) in said


# --------------------------------------------------------------------------
# T30 -- a server installed from the retired fork, opened by the new entry
# --------------------------------------------------------------------------


OLD_FORK_SERVER = {
    "src/tortoise-wow/sql/character_updates/20260708055500_ai_playerbot_random_bots_index.sql": (
        "-- the fork's own directory, which the Penqle core does not have\n"
    ),
    "src/tortoise-wow/modules/mod-playerbots/sql/characters/ai_playerbot_random_bots.sql": (
        "-- the vendored cmangos playerbots, which the Penqle core does not have\n"
    ),
    "src/tortoise-wow/src/modules/Eluna/LuaEngine.h": "// the fork's one submodule\n",
    "etc/mangosd.conf": "[MangosdConf]\nAutoHonorRestart = 0\nSOAP.Enabled = 1\n",
    "etc/aiplayerbot.conf": "AiPlayerbot.MinRandomBots = 500\n",
    ".db_password": "tortoise-0123456789abcdef\n",
}
"""A server directory in the shape the retired fork left behind.

Every path is one the OLD `wow-tortoise` entry wrote or cloned and the new one
does not: the two SQL directories its plan globbed, the Eluna checkout its
second source cloned, and a `mangosd.conf` carrying a `SOAP.Enabled` -- a key the
Penqle core lacked until PR #491 and has again since T86. `~/tortoise-vm` on
`yulon-arch` and the owner's own install on m910q are both this shape.
"""


def _old_fork_install(tmp_path: Path) -> tuple[Path, Path]:
    server_dir = tmp_path / "tortoise-wow-server"
    for relative, text in OLD_FORK_SERVER.items():
        target = server_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    client_dir = tmp_path / "TurtleWoW"
    (client_dir / "Interface" / "AddOns").mkdir(parents=True)
    return server_dir, client_dir


def test_a_server_installed_from_the_retired_fork_still_opens_its_tabs(tmp_path: Path) -> None:
    """The owner's condition on replacing the entry: existing installs keep opening.

    T30 replaced `wow-tortoise` rather than adding a second game -- the owner's
    answer, 2026-09-11 -- so the ONE entry now describes the Penqle core while
    two servers on this desk (`~/tortoise-vm` on `yulon-arch`, and m910q's) were
    built from the fork it replaced. Their `state.json` record is a game id and
    two folders, so they open through exactly this route.

    What has to survive is every seam the tab draws itself from: the controller,
    the SQL runner over `.db_password`, accounts, play, the bot browser, the
    console probe and the manifest store. None of them reads a source rev -- they
    read this entry's containers, schemas and columns, which did not move
    between the two trees (`tw_char`/`tw_logon`, `mangos_sha`, `RNDBOT`).

    Catches a factory that starts reading something only the new stack has, and
    a tab that refuses to open on a folder whose `src/` is the old shape.
    """
    server_dir, client_dir = _old_fork_install(tmp_path)
    known = state.KnownInstall(game="wow-tortoise", server_dir=server_dir, client_dir=client_dir)
    app_state = state.AppState()
    app_state.remember(known)
    reloaded = state.AppState.model_validate_json(app_state.model_dump_json()).find(
        "wow-tortoise", server_dir
    )
    assert reloaded is not None, "the record does not survive a round trip through state.json"

    services = ControllerServices.for_entry(
        load_catalog().get(reloaded.game),
        reloaded.server_dir,
        client_dir=reloaded.client_dir,
        wsl_distro=reloaded.wsl_distro,
    )
    assert services.controller is not None
    assert services.send_console is not None
    assert services.accounts is not None
    assert services.play is not None
    assert services.bots is not None
    assert services.store is not None and services.applier is not None
    assert services.channel_setup is not None, (
        "this entry's channel is SOAP again (T86); a fork install reaches its world through "
        "the same channel setup a new one does -- the retired fork had SOAP too, and a "
        "bot-helpers install (no SOAP in its binary, and no path here moves its pin: a "
        "rebuild reuses its own src/) answers `could not ask` until it is reinstalled"
    )
    assert services.rebuild is not None, (
        "the rebuild press is what puts such an install onto the new stack when its owner "
        "chooses to; withholding it would strand the folder"
    )


def test_the_marker_rule_presses_are_withheld_from_a_fork_install_as_from_any_other(
    tmp_path: Path,
) -> None:
    """Adopt and Updates are gone for every Tortoise install, fork-built or not.

    They were offered because the plan declared one `rerun_on_marked` phase --
    `character updates`, over `sql/character_updates/`, which is on the fork's
    tree and not on the Penqle core. With the phase gone the plan declares none,
    so `native.update_phases()` answers `()` and both controls are withheld
    rather than pressed and then explaining themselves. That is the SAME rule as
    before applied to a different plan, not a new exception for old folders, and
    it is asserted on a folder that really has the directory those presses
    applied -- which is the one place a reader might expect the app to notice.

    Nothing about the install's own databases is read to decide this, and that
    matters: the press being absent means the marker row is never consulted, so
    a fork install cannot have DDL run against its live `tw_char` by an app
    upgrade. T29's "older plan hash" arm is reachable only through these two
    presses; with none offered, that arm is not reached at all on this game.
    """
    server_dir, client_dir = _old_fork_install(tmp_path)
    assert (server_dir / "src/tortoise-wow/sql/character_updates").is_dir()
    services = ControllerServices.for_entry(
        load_catalog().get("wow-tortoise"), server_dir, client_dir=client_dir
    )
    assert services.updates is None
    assert services.adopt is None
    assert native.update_phases(load_catalog().get("wow-tortoise")) == ()


# --------------------------------------------------------------------------
# T30 -- the Tortoise tab's client folder, for the two Turtle addons
# --------------------------------------------------------------------------


def _tortoise_services(tmp_path: Path, client_dir: Path | None):
    server_dir = tmp_path / "tw"
    server_dir.mkdir(parents=True, exist_ok=True)
    return ControllerServices.for_entry(
        load_catalog().get("wow-tortoise"), server_dir, client_dir=client_dir
    )


def test_the_tortoise_applier_is_handed_the_client_folder_its_addons_are_written_into(
    tmp_path: Path,
) -> None:
    """The one keyword this factory swallowed, and what it cost.

    `tortoise_modules.applier()` has taken `client_dir` since 8.7d and
    `_for_tortoise` was the only game factory that did not pass it (tbc at
    `:1441`, vanilla at `:1603`), so a manifest `client` step on this game
    reported "no client dir configured" and copied nothing. Nothing in
    `manifests/wow-tortoise/` declared one until T30 added the two Turtle
    addons, which is how the gap sat there unseen.

    Asserted on the applier the tab is really handed, because the keyword is
    accepted either way: an applier built without it is not an error, it is an
    applier that silently skips every client step it is ever given.
    """
    client = tmp_path / "TurtleWoW"
    (client / "Interface" / "AddOns").mkdir(parents=True)
    services = _tortoise_services(tmp_path, client)
    assert services.applier is not None
    assert services.applier.client_dir == client, (
        "the Tortoise tab's applier has no client folder, so every addon it installs is "
        "copied nowhere and reported as skipped"
    )


def test_a_client_folder_with_no_interface_directory_is_not_written_into(
    tmp_path: Path,
) -> None:
    """The refusal, named: `Interface/` is the folder the GAME ships.

    `_ApplyEngine._client()` joins `Interface/AddOns/<name>` onto whatever it is
    handed and creates every missing parent, so without this guard a folder that
    is not a WoW client would quietly gain an `Interface/AddOns/TortoiseBotsManager`
    and the install would report success -- while the client the user actually
    plays never got the addon.

    Three folders, one rule, and the first two must not read the same to the
    applier as the third: a record with no client dir at all, a folder with no
    `Interface/`, and a real client. The refusal costs a user one launch of the
    game; guessing costs them files in a folder the app was told to treat as
    their own.
    """
    assert _tortoise_services(tmp_path / "none", None).applier.client_dir is None
    bare = tmp_path / "not-a-client"
    (bare / "Data").mkdir(parents=True)
    assert _tortoise_services(tmp_path / "bare", bare).applier.client_dir is None, (
        "a folder with no Interface/ was accepted; the addons would be written into a "
        "directory tree this app created inside it"
    )
    real = tmp_path / "TurtleWoW"
    (real / "Interface").mkdir(parents=True)
    assert _tortoise_services(tmp_path / "real", real).applier.client_dir == real, (
        "Interface/ without AddOns/ is a client no addon has been installed into yet, which "
        "is the case this guard must NOT refuse -- AddOns/ is the folder an addon creates"
    )


# --------------------------------------------------------------------------
# T36 -- a client folder can be set, changed or cleared on an install
# --------------------------------------------------------------------------


class _FakeClientDir:
    """`ControllerServices.set_client_dir` (T36): records writes, raises on demand."""

    def __init__(self, error: OSError | None = None) -> None:
        self.written: list[Path | None] = []
        self._error = error

    def __call__(self, client_dir: Path | None) -> None:
        self.written.append(client_dir)
        if self._error is not None:
            raise self._error


def _client_dir_view(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    client_dir: Path | None = None,
    fake: _FakeClientDir | None = None,
    pick_client_dir: Callable[..., Path | None] = lambda *_: None,
) -> tuple[ControllerView, _FakeClientDir]:
    """A tab wired with the T36 write seam, over the real factory wiring.

    `ControllerServices.for_entry()` and not the `_services()` fake: the row's
    button labels and the press's refusal/warning rules read `entry.client`
    and (through `preflight.client_spec_for()`) `entry.install.native`, none of
    which the docker-free fake carries, and the real factory needs no daemon
    to build.
    """
    services = ControllerServices.for_entry(entry, server_dir, client_dir=client_dir)
    fake = fake if fake is not None else _FakeClientDir()
    services.set_client_dir = fake
    view = ControllerView(entry, services, status_poll_ms=0, pick_client_dir=pick_client_dir)
    return view, fake


def test_the_client_folder_row_reads_its_three_sentences(qapp: object, tmp_path: Path) -> None:
    """None / recorded / recorded-without-`Interface/` (T36 DoD 1)."""
    view_none, _ = _client_dir_view(WOTLK, tmp_path / "none")
    assert view_none.client_dir_label.text() == "Client folder: none — addons and Play need one"

    bare = tmp_path / "bare-client"
    bare.mkdir()
    view_bare, _ = _client_dir_view(WOTLK, tmp_path / "bare", client_dir=bare)
    assert view_bare.client_dir_label.text() == (
        f"Client folder: {bare} — no Interface/ folder yet — start the game once before "
        "installing addons"
    )

    real = tmp_path / "real-client"
    (real / "Interface").mkdir(parents=True)
    view_real, _ = _client_dir_view(WOTLK, tmp_path / "real", client_dir=real)
    assert view_real.client_dir_label.text() == f"Client folder: {real}"
    # Mutation: in `_client_dir_row_text()`, drop the `Interface/` branch so
    # any non-None folder reads "Client folder: {client_dir}" -- the
    # `view_bare` assertion above fails, reading the real-client sentence
    # instead of the "no Interface/" one.


def test_the_client_folder_buttons_read_set_or_change_and_forget_appears_once_recorded(
    qapp: object, tmp_path: Path
) -> None:
    """The button labels, exactly as they read (T36 DoD 1)."""
    view_none, _ = _client_dir_view(WOTLK, tmp_path / "none")
    assert view_none.set_client_dir_button is not None
    assert view_none.set_client_dir_button.text() == "Set client folder…"
    assert view_none.forget_client_dir_button is not None
    assert view_none.forget_client_dir_button.isHidden()

    real = tmp_path / "real-client"
    (real / "Interface").mkdir(parents=True)
    view_real, _ = _client_dir_view(WOTLK, tmp_path / "real", client_dir=real)
    assert view_real.set_client_dir_button.text() == "Change client folder…"
    assert not view_real.forget_client_dir_button.isHidden()
    # Mutation: in `_build_server_tab()`, hardcode
    # `has_client = self.services.client_dir is not None` to `False` -- the
    # `view_real` button would still read "Set client folder…" and its
    # Forget button would stay hidden, both against this test.


def test_set_client_dir_none_hides_the_row_and_its_buttons(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A tab built with no write seam gets no client-folder controls (T36 DoD 3)."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.set_client_dir_button is None
    assert view.forget_client_dir_button is None
    assert view.client_dir_label.isHidden()
    # Mutation: change the `if self.services.set_client_dir is not None:` guard
    # in `_build_server_tab()` to build the buttons unconditionally -- both
    # `is None` assertions above fail.


def test_a_refused_client_folder_writes_nothing_and_shows_the_check_sentence(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `Data/`: `clientdir.validate()`'s own refusal, TBC's `ClientSpec` (T36 DoD 2)."""
    warned: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        controller_view_module.QMessageBox, "warning", lambda *a, **k: warned.append(a)
    )
    not_a_client = tmp_path / "not-a-client"
    not_a_client.mkdir()
    view, fake = _client_dir_view(TBC, tmp_path / "server", pick_client_dir=lambda *_: not_a_client)
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    view.change_client_dir()

    assert fake.written == [], "a folder with no Data/ directory was written anyway"
    assert failures and "not a game client" in failures[0]
    assert warned and "not a game client" in warned[0][2]
    # Mutation: in `change_client_dir()`, drop the `if not report.ok(): ...
    # return` guard -- `fake.written` gains the folder even though the check
    # refused it.


def test_a_warned_client_folder_writes_only_after_yes(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The static `QMessageBox.question()`'s bare `int` (T33): `==`, never `is`."""
    warn_client = tmp_path / "TurtleWoW"
    data = warn_client / "Data"
    data.mkdir(parents=True)
    (data / "patch.MPQ").write_bytes(b"")  # 1 of 5: too few is a WARN, zero is a refusal
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    view, fake = _client_dir_view(
        TORTOISE, tmp_path / "server", pick_client_dir=lambda *_: warn_client
    )

    view.change_client_dir()

    assert fake.written == [warn_client]
    # Mutation: change `change_client_dir()`'s `said_yes(answer)` to
    # `answer is QMessageBox.StandardButton.Yes` -- the bare `int` the fake
    # returns above is never `is` the enum member, so this write never happens.


def test_a_warned_client_folder_answered_no_writes_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of "only after Yes": No leaves the record untouched."""
    warn_client = tmp_path / "TurtleWoW"
    data = warn_client / "Data"
    data.mkdir(parents=True)
    (data / "patch.MPQ").write_bytes(b"")  # 1 of 5: a WARN this press still has to ASK about
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.No),
    )
    view, fake = _client_dir_view(
        TORTOISE, tmp_path / "server", pick_client_dir=lambda *_: warn_client
    )

    view.change_client_dir()

    assert fake.written == []
    # Mutation: drop the `if not said_yes(answer): return` guard in
    # `change_client_dir()` -- the folder is written even though No was
    # answered.


def test_the_seam_is_called_with_the_picked_path_then_client_dir_changed_is_emitted(
    qapp: object, tmp_path: Path
) -> None:
    """T36 DoD 4: no `ClientSpec` at all (WotLK) writes straight through once it has a `Data/`."""
    server_dir = tmp_path / "server"
    chosen = tmp_path / "new-client"
    (chosen / "Data").mkdir(parents=True)
    view, fake = _client_dir_view(WOTLK, server_dir, pick_client_dir=lambda *_: chosen)
    seen: list[tuple[str, object, object]] = []
    view.client_dir_changed.connect(lambda g, s, c: seen.append((g, s, c)))

    view.change_client_dir()

    assert fake.written == [chosen]
    assert seen == [("wow-wotlk", server_dir, chosen)]
    # Mutation: drop the final `self.client_dir_changed.emit(...)` line in
    # `change_client_dir()` -- `fake.written` still gains the folder, but
    # `seen` stays empty.


def test_a_failing_seam_shows_the_error_and_emits_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OSError` from the write seam (T36 DoD 4), the same shape T34's forget answers."""
    warned: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        controller_view_module.QMessageBox, "warning", lambda *a, **k: warned.append(a)
    )
    chosen = tmp_path / "new-client"
    (chosen / "Data").mkdir(parents=True)
    fake = _FakeClientDir(error=OSError("config dir is read-only"))
    view, _ = _client_dir_view(
        WOTLK, tmp_path / "server", fake=fake, pick_client_dir=lambda *_: chosen
    )
    seen: list[object] = []
    failures: list[str] = []
    view.client_dir_changed.connect(lambda *a: seen.append(a))
    view.action_failed.connect(failures.append)

    view.change_client_dir()

    assert seen == [], "the tab was told to rebuild over a write that never happened"
    assert failures and "config dir is read-only" in failures[0]
    assert warned and "config dir is read-only" in warned[0][2]
    # Mutation: drop the `except OSError` branch in `change_client_dir()` --
    # the exception propagates instead of being shown, and `seen`/`failures`
    # are never populated the way this test expects.


def test_forget_client_dir_writes_none_and_emits_the_rebuild_signal(
    qapp: object, tmp_path: Path
) -> None:
    """ "Forget client folder": no confirmation, unlike "Forget this install…" (T36 DoD 1/4)."""
    server_dir = tmp_path / "server"
    real = tmp_path / "real-client"
    (real / "Interface").mkdir(parents=True)
    view, fake = _client_dir_view(WOTLK, server_dir, client_dir=real)
    seen: list[tuple[str, object, object]] = []
    view.client_dir_changed.connect(lambda g, s, c: seen.append((g, s, c)))
    assert view.forget_client_dir_button is not None

    view.forget_client_dir()

    assert fake.written == [None]
    assert seen == [("wow-wotlk", server_dir, None)]
    # Mutation: in `forget_client_dir()`, call `self.services.set_client_dir(
    # self.services.client_dir)` instead of `(None)` -- `fake.written` reads
    # `[real]` rather than `[None]` and this fails.


# ----------------------------------------- T36 round 2 review's four fixes


def test_busy_disables_the_three_client_folder_buttons(qapp: object, tmp_path: Path) -> None:
    """Round 2 review fix 1: the same lock `_set_busy()` already puts on Uninstall."""
    real = tmp_path / "real-client"
    (real / "Interface").mkdir(parents=True)
    view, _ = _client_dir_view(WOTLK, tmp_path / "server", client_dir=real)
    assert view.set_client_dir_button is not None
    assert view.forget_client_dir_button is not None

    view._set_busy(True)

    assert not view.set_client_dir_button.isEnabled()
    assert not view.forget_client_dir_button.isEnabled()

    view._set_busy(False)

    assert view.set_client_dir_button.isEnabled()
    assert view.forget_client_dir_button.isEnabled()
    # Mutation: drop the four `set_client_dir_button`/`forget_client_dir_button`
    # `setEnabled()` lines from `_set_busy()` -- both buttons stay enabled
    # through the `_set_busy(True)` call above.


def test_a_busy_press_writes_nothing_on_either_handler(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2 review fix 1: a disabled `QPushButton` is not the only way in — `rebuild_server()`'s
    own shape, `self._busy` checked again inside the handler.
    """
    told: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        controller_view_module.QMessageBox, "information", lambda *a, **k: told.append(a)
    )
    real = tmp_path / "real-client"
    (real / "Interface").mkdir(parents=True)
    chosen = tmp_path / "new-client"
    (chosen / "Data").mkdir(parents=True)
    view, fake = _client_dir_view(
        WOTLK, tmp_path / "server", client_dir=real, pick_client_dir=lambda *_: chosen
    )
    view._set_busy(True)

    view.change_client_dir()
    view.forget_client_dir()

    assert fake.written == [], "a write went through while another action was running"
    assert len(told) == 2, "each press should have said something else is running"
    # Mutation: drop the `if self._client_dir_busy(): return` guard from
    # `change_client_dir()` and `forget_client_dir()` -- `fake.written` gains
    # entries despite `_set_busy(True)` above (the buttons are disabled, but
    # nothing stops a call reaching the handler directly, as this test does).


def test_zero_archives_is_refused_even_after_yes(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2 review fix 2: an empty `Data/` is not "a few too few", it is nothing to extract."""
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    empty_client = tmp_path / "TurtleWoW"
    (empty_client / "Data").mkdir(parents=True)
    view, fake = _client_dir_view(
        TORTOISE, tmp_path / "server", pick_client_dir=lambda *_: empty_client
    )
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    view.change_client_dir()

    assert fake.written == [], "an empty Data/ was written even after a Yes"
    assert failures and "0 MPQ archives" in failures[0]
    # Mutation: in `_mpq_archive_count()`, `return None` unconditionally --
    # the zero-archive refusal never fires, and this folder is written after
    # Yes exactly like `test_a_warned_client_folder_writes_only_after_yes`.


def test_the_server_folder_or_anything_inside_it_is_refused_before_validation(
    qapp: object, tmp_path: Path
) -> None:
    """Round 2 review fix 3: Uninstall removes that whole tree, client folder included."""
    server_dir = tmp_path / "server"
    sub = server_dir / "client-inside"
    sub.mkdir(parents=True)
    sibling = tmp_path / "client-next-door"
    (sibling / "Data").mkdir(parents=True)

    for picked in (server_dir, sub):
        view, fake = _client_dir_view(WOTLK, server_dir, pick_client_dir=lambda *_, p=picked: p)
        failures: list[str] = []
        view.action_failed.connect(failures.append)
        view.change_client_dir()
        assert fake.written == [], f"{picked} was written; it is the server folder or inside it"
        assert failures and "cannot be the server folder or inside it" in failures[0]

    # The sibling is unrelated to the server tree and must still be accepted.
    view, fake = _client_dir_view(WOTLK, server_dir, pick_client_dir=lambda *_: sibling)
    view.change_client_dir()
    assert fake.written == [sibling]
    # Mutation: drop the `if chosen.resolve().is_relative_to(server_dir.resolve()):
    # ... return` guard from `change_client_dir()` -- the server folder and its
    # subfolder are both written above instead of refused.


def test_wotlk_requires_at_least_a_data_folder(qapp: object, tmp_path: Path) -> None:
    """Round 2 review fix 4: no `ClientSpec` still needs SOME evidence this is a WoW client."""
    empty = tmp_path / "not-a-client"
    empty.mkdir()
    view, fake = _client_dir_view(WOTLK, tmp_path / "server", pick_client_dir=lambda *_: empty)
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    view.change_client_dir()

    assert fake.written == [], "a folder with no Data/ was written for a game with no ClientSpec"
    assert failures and "is not a WoW client" in failures[0]

    real = tmp_path / "real-client"
    (real / "Data").mkdir(parents=True)
    view2, fake2 = _client_dir_view(WOTLK, tmp_path / "server2", pick_client_dir=lambda *_: real)
    view2.change_client_dir()
    assert fake2.written == [real]
    # Mutation: drop the `elif not (chosen / clientdir.DATA_DIR).is_dir(): ...
    # return` branch in `change_client_dir()` -- `empty` above is written
    # instead of refused.


def test_the_row_says_the_folder_is_missing_rather_than_no_interface(
    qapp: object, tmp_path: Path
) -> None:
    """Non-blocking round 2 note: a moved/deleted client needs its own sentence, not "start the
    game once" -- that instruction cannot be followed on a folder that is not there.
    """
    gone = tmp_path / "gone-client"
    view, _ = _client_dir_view(WOTLK, tmp_path / "server", client_dir=gone)
    assert view.client_dir_label.text() == f"Client folder: {gone} — the folder is missing"
    # Mutation: drop the `if not client_dir.is_dir():` branch from
    # `_client_dir_row_text()` -- the missing folder falls into the
    # no-`Interface/` sentence instead.


# --------------------------------------------------------------------------
# 8.8 -- Add to Steam...
# --------------------------------------------------------------------------


class _FakeSteam:
    """The 8.8 seam, standing in for the thing that writes the user's profile.

    A fake and not the real `SteamShortcuts` for `Uninstall`'s reason: a view
    test that reached the real one would be a view test that edits the Steam
    library of whoever is running it.
    """

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.presses = 0

    def add(self) -> object:
        self.presses += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _report(tmp_path: Path) -> steam.AddReport:
    config = tmp_path / "userdata/18347166/config"
    return steam.AddReport(
        entries=("Turtle WoW", "Turtle WoW Server"),
        path=config / "shortcuts.vdf",
        backup=config / "shortcuts.vdf.yulon-bak-20260910-200500",
        replaced=False,
        artwork=tuple(config / "grid" / f"{i}.png" for i in range(6)),
        compat_tool="GE-Proton11-6-x86_64",
        compat_path=tmp_path / "config/config.vdf",
        compat_backup=None,
    )


def test_the_add_to_steam_button_says_what_it_wrote(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The confirmation names both entries, the file, the backup and the tool."""
    services = _services(ps, tmp_path, [])
    services.steam = _FakeSteam(_report(tmp_path))
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.steam_button is not None
    view.steam_button.click()

    said = view.steam_label.text()
    assert "Turtle WoW" in said and "Turtle WoW Server" in said
    assert "shortcuts.vdf" in said and "yulon-bak-20260910-200500" in said
    assert "GE-Proton11-6-x86_64" in said
    assert view.steam_button.isEnabled()


def test_a_refused_add_to_steam_is_readable_on_screen_not_just_emitted(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Steam running is the refusal 8.8's definition of done names by hand.

    On the label as well as on the signal, and the button comes back enabled:
    the remedy is to close Steam and press it again, and a control that stayed
    grey would be telling the user to do something they then cannot do.
    """
    services = _services(ps, tmp_path, [])
    services.steam = _FakeSteam(steam.SteamRefusal(steam.RUNNING))
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    refused: list[str] = []
    view.action_failed.connect(refused.append)

    assert view.steam_button is not None
    view.steam_button.click()

    assert "Steam is running" in view.steam_label.text()
    assert refused == [steam.RUNNING]
    assert view.steam_button.isEnabled()


def test_there_is_no_add_to_steam_button_at_all_without_the_seam(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Absent, not disabled -- what `_steam_seam()` answers off Linux."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)

    assert view.services.steam is None
    assert view.steam_button is None
    assert not view.steam_label.isVisible()


@pytest.mark.parametrize("platform_id", ["windows", "macos"])
def test_the_steam_seam_is_none_on_windows_and_macos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform_id: str
) -> None:
    """The one platform question in this file, asked in one place."""
    monkeypatch.setattr(controller_view_module.platform, "detect", lambda: platform_id)

    assert controller_view_module._steam_seam(WOTLK, tmp_path, tmp_path / "client") is None


def test_the_steam_seam_is_built_on_linux_and_carries_the_client_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The client entry is a path into the folder the user picked, from `state.json`."""
    monkeypatch.setattr(controller_view_module.platform, "detect", lambda: "linux")

    seam = controller_view_module._steam_seam(WOTLK, tmp_path, tmp_path / "client")

    assert seam is not None
    assert seam.client_dir == tmp_path / "client"
    assert seam.game == WOTLK.name


def test_neither_one_line_sink_can_show_a_control_character(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T35's markers must never reach a `QLabel` or the module report.

    These two sinks are the reason the marking is done at the install engine's
    bridges and not inside `docker.run_attached()`: they are fed by the same
    `run_attached()` calls the install uses, and neither is a `LogPanel` — one
    is a one-line status label, the other a plain report pane — so neither
    strips anything. `\\x1e` renders as a box glyph in both.

    Belt on top of braces, and asserted because the cost of being wrong is a
    box glyph in front of every line of a database import with nothing on
    screen to explain it.

    **Asserted as equality and not as "no `\x1e` in it"**, which was the first
    version of this test and let the mutation through. `\x1e` is WHITESPACE to
    Python: `"\x1etool >> x".strip()` is `"tool >> x"`, so the slot that only
    stripped removed the separator and left the kind's name standing in front of
    every line. Measured while this test was written, against the real slot.

    Mutation: drop `lines.parse()` from either slot and that half fails with
    `tool ` in front of the line.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    view._import_line(log_lines.TOOL + ">> Applying update 2026_01_01_00.sql")
    view._module_sql_line(log_lines.TOOL + ">> Applying mod-playerbots.sql")

    assert view.problem_label.text().splitlines()[-1] == ">> Applying update 2026_01_01_00.sql"
    assert view.module_report.toPlainText().splitlines()[-1] == ">> Applying mod-playerbots.sql"


def _installed_view(ps: _Ps, tmp_path: Path, **families: frozenset[str]) -> ControllerView:
    """A WotLK view whose install has these clones, per manifest family.

    Per family and not one set, because `apply.CLONE_DIRS` gives each family its
    own folder: a module lands in `modules/`, an ale and a keg in
    `ale_scripts/`, a mod in `sql_scripts/clones/`.
    """
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "installed_modules", lambda: dict(families))
    return ControllerView(WOTLK, services, status_poll_ms=0)


def _panel_ids(view: ControllerView) -> list[str]:
    return [row.data.id for row in view.modules_panel.rows()]


def _marked(view: ControllerView) -> list[object]:
    """The rows the tab draws the `Installed` badge on — the widgets, not their ids.

    Read off the BADGE and not off a substring of the row's words: T41's own
    review found that `INSTALLED_MARK in row` also matched a description that
    happened to contain the glyph, and a name or a description containing the
    word "Installed" would be the same fault in the new surface.

    The WIDGETS, because an id does not name a row: `modules_panel.row(id)`
    resolves a bare id in `FAMILY_FILES` order, so asking it which family a
    marked id was in answers about a different row whenever two families share
    an id -- which is exactly the case `mod-ale` makes below (round 2).
    """
    return [row for row in view.modules_panel.rows() if row.badge_label.text() == BADGE_INSTALLED]


def test_the_modules_tab_says_which_modules_are_installed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T41, 2026-09-12: "None of modules detected lol", on an install that had some.

    Re-seeded for T42 from `test_the_modules_list_says_which_modules_are_
    installed`: the leading `INSTALLED_MARK` on a list line is now the row's own
    `Installed` badge, read off the widget.

    `reload_modules()` filled the list from the manifest STORE and read nothing
    off disk, so the tab showed what this game COULD install and never what it
    had. Measured on the live install on `yulon-win11`: `mod-playerbots` sits in
    `modules/` and all 41 catalog rows looked identical to a server with none.
    """
    view = _installed_view(ps, tmp_path, module=frozenset({"mod-transmog"}))
    marked = _marked(view)

    # Exactly one row, and it is the catalog's own transmog row -- not an
    # erroneous extra "not in catalog" row beside an unmarked one, which an
    # `any(...)` assertion would have accepted.
    assert [row.data.id for row in marked] == ["mod-transmog"], marked
    row = view.modules_panel.row("mod-transmog")
    assert row.data.catalogued is True and NOT_IN_CATALOG not in row.data.description
    assert len(view.modules_panel.rows()) > 5


def test_the_installed_row_is_drawn_above_the_ones_that_are_not(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T42's first line, end to end through the real store and the real panel.

    TWO claims, and round 2 separated them because this test used to make one
    and name the other. What a person SEES is `drawn_rows()`, read off the
    card's layouts -- `_FamilyCard.fill()` puts the installed half in its own
    box above the available one. What the BUILDER decided is `rows()`, the order
    `set_rows()` was handed. Both must put `mod-transmog` first, and each has
    its own mutation.

    Mutation (both halves): drop the installed-first split in
    `build_module_rows` and `mod-transmog` falls back to wherever the catalog
    index happens to put it, which on the shipped WotLK tree is not the top.
    The card's own split cannot be told apart HERE -- rows that arrive already
    sorted are drawn the same either way -- so it has its own test, which hands
    the panel an order the builder would never produce
    (`test_the_cards_draw_the_installed_half_above_the_available_half`).
    """
    view = _installed_view(ps, tmp_path, module=frozenset({"mod-transmog"}))

    drawn = [r.data for r in view.modules_panel.drawn_rows() if r.data.family == "module"]
    assert drawn[0].id == "mod-transmog", [m.id for m in drawn[:3]]
    assert drawn[0].installed is True
    built = [r.data.id for r in view.modules_panel.rows() if r.data.family == "module"]
    assert built[0] == "mod-transmog", built[:3]
    assert view.modules_panel.available_open("module") is False, "it has one, so it collapses"


def test_a_module_on_disk_the_catalog_never_heard_of_still_gets_a_row(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """It is installed, whatever the catalog thinks.

    Re-seeded for T42 from the T41 test of the same name: the row is a
    `ModuleRow` with `catalogued=False` rather than a list line carrying the
    sentence, and it is the panel that is asked.

    `apply_module.module_updates()` already takes this position for its own
    rows — "A module on disk that the store has never heard of still gets a
    row" — and a list that silently omits somebody's hand-cloned module is the
    same "None of modules detected" in a smaller place.
    """
    view = _installed_view(ps, tmp_path, module=frozenset({"mod-something-homemade"}))

    assert _panel_ids(view).count("mod-something-homemade") == 1
    row = view.modules_panel.row("mod-something-homemade")
    assert row.badge_label.text() == BADGE_INSTALLED
    assert row.data.description == NOT_IN_CATALOG
    # No manifest is invented for it, so there is nothing for a press to act on
    # -- and the row carries no press at all.
    assert "mod-something-homemade" not in view._manifests
    assert row.install_button is None and row.remove_button is None


def test_a_game_with_no_installed_modules_seam_lists_the_catalog_unchanged(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The three CMaNGOS games have no modules folder; every row reads Not installed.

    Re-seeded for T42: the absence of a mark is now the `Not installed` badge,
    which is a stronger assertion — a row that said nothing at all would pass
    the old one.
    """
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "installed_modules", None)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    rows = view.modules_panel.rows()
    assert rows, "the catalog still lists"
    assert {row.badge_label.text() for row in rows} == {BADGE_NOT_INSTALLED}


def test_the_context_menu_on_an_uncatalogued_row_says_why_nothing_happened(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Review, 2026-09-12: those rows were inert and silent.

    Re-seeded for T42 from `test_pressing_install_on_an_uncatalogued_row_says_
    why_nothing_happened`. The row now has no buttons at all, so the press that
    has to be answered is the context menu's — which is the only one it offers.

    T41 added rows for modules on disk the catalog has never heard of. They
    carry no manifest, so `_module_action` returned at its first line and the
    press did nothing and said nothing — a control that looks broken, which is
    the same complaint this ticket started from, one step further along.
    """
    from yulon.ui.controller_view import UNCATALOGUED_PRESS

    view = _installed_view(ps, tmp_path, module=frozenset({"mod-something-homemade"}))
    view.modules_panel.select("mod-something-homemade")

    assert view.selected_manifest() is None, "the row must carry no manifest"
    assert view._selected_row_is_uncatalogued() is True
    view._module_action("install")
    assert view.module_report.toPlainText() == UNCATALOGUED_PRESS, view.module_report.toPlainText()


def test_a_family_is_marked_from_its_own_clone_folder_not_from_modules(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Review, 2026-09-12: `apply.CLONE_DIRS` gives each family a different folder.

    Re-seeded for T42 against the panel. A module lands in `modules/`, an ale
    and a keg in `ale_scripts/`, a mod in `sql_scripts/clones/`. Reading
    `modules/` for all four marked an ale installed because a MODULE of the same
    id was, and never marked a real ale at all.
    """
    # `mod-ale` is a shipped module id. Claim it is present in the ALE family's
    # folder and absent from the module family's: the module row must stay
    # unmarked, and a row must appear for the ale clone instead.
    view = _installed_view(ps, tmp_path, module=frozenset(), ale=frozenset({"mod-ale"}))
    marked = _marked(view)

    assert marked, "the ale clone is installed and must be marked somewhere"
    families = {row.data.family for row in marked}
    assert families == {"ale"}, families


def test_a_clone_matched_in_one_family_is_not_listed_again_as_unknown_in_another(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Measured live, 2026-09-12: `bmah` appeared twice.

    Re-seeded for T42 against the panel. `apply.CLONE_DIRS` puts ale and keg in
    one folder, so a keg's clone is read into BOTH families' sets. The keg
    manifest matched it and marked its row; the ale copy then looked like a
    module nobody had a manifest for and got an "installed here — not in this
    game's catalog" row of its own, for a thing the list had already named one
    line up.
    """
    view = _installed_view(ps, tmp_path, ale=frozenset({"bmah"}), keg=frozenset({"bmah"}))
    bmah = [r for r in view.modules_panel.rows() if r.data.id == "bmah"]
    assert len(bmah) == 1, bmah
    assert bmah[0].data.family == "keg" and bmah[0].data.catalogued is True


def test_the_forget_button_appears_even_when_the_status_poll_cannot_reach_docker(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The button's whole case is an install that is gone — Docker usually with it (T54).

    Reported on 0.8.65-Public. A user deleted `E:\\Games\\Yulon Wotlk` by hand and
    uninstalled Docker Desktop. Uninstall refused and told them exactly what to
    do next:

        ... If the folder is gone for good, "Forget this install…" drops this tab.

    and they answered: *"Where is 'Forget this install' — Cannot find that at
    all within Yulon"*.

    They could not find it because it cannot be shown to them.
    `_update_forget_visibility()` had ONE caller, on the success path of the
    Docker status poll; with Docker gone every poll takes `_status_failed()`,
    which sets a label and returns. The control built for "everything is gone"
    was reachable only while enough was still there to answer.

    The reveal needs nothing from Docker: `_forget_is_eligible()` asks
    `wsl_distro` and `folder_is_gone()`, both local.
    """
    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)

    def no_docker() -> NoReturn:
        raise RuntimeError("Cannot connect to the Docker daemon")

    view.services.controller.status = no_docker  # type: ignore[method-assign]
    shutil.rmtree(tmp_path)

    view.refresh_status()

    assert view.forget_install_button is not None
    assert (
        not view.forget_install_button.isHidden()
    ), "the poll failed, so the user is told to press a button they cannot see"
    # The failure is still reported — this must not paper over Docker being gone.
    assert "Docker not reachable" in view.status_label.text()


def test_the_drawn_row_for_a_keg_clone_is_marked_installed_and_appears_once(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The widget half of the test above: what the ROW shows, not what the data says.

    These four assertions lost their own `def` line in a merge and were fused
    onto the end of an unrelated T54 test, whose view has no `bmah` at all --
    so they asserted `Installed` against a row that was `Not installed` and
    broke the suite for everyone. Upstream CI could not see it, because `black`
    runs before `pytest` and was failing on a blank line.

    Restored here with the fixture they were written for: ale and keg share
    `ale_scripts/`, so a keg's clone is read into both families' sets, and the
    row must be drawn once, as the keg, marked installed.
    """
    view = _installed_view(ps, tmp_path, ale=frozenset({"bmah"}), keg=frozenset({"bmah"}))

    assert _panel_ids(view).count("bmah") == 1
    row = view.modules_panel.row("bmah")
    assert row.badge_label.text() == BADGE_INSTALLED
    assert row.data.catalogued is True and row.data.family == "keg"


# --------------------------------------------------- T42: the session's own facts


def _deliver_report(view: ControllerView, report: ApplyReport) -> None:
    """Deliver an `ApplyReport` the way a real press does -- with the manifest set.

    `_module_done()` reads `_acting_on` to key this session's chips by
    `(family, id)` (round 2): nothing makes an id unique across families. Since
    T48 the report carries its family too, and this sets the manifest OF that
    family, which is what a real press does. Both live routes set `_acting_on`
    immediately before their `_run()`, so a test that called `_module_done()`
    bare was exercising a path no press reaches.

    The manifest comes out of the view's own `_manifests` where the shipped
    catalog has one, so these tests keep using the ids they always used; a
    stand-in is built only for an id it does not carry.
    """
    family = report.family
    manifest = view._manifests.get((family, report.item_id))
    if manifest is None:
        manifest = Manifest(
            id=report.item_id,
            name=report.item_id,
            type=cast(ManifestType, family),
            game="wow-wotlk",
            description="x",
        )
    view._acting_on = manifest
    view._module_done(report)


def _wotlk_modules_view(ps: _Ps, tmp_path: Path, **families: frozenset[str]) -> ControllerView:
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "installed_modules", lambda: dict(families))
    return ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts: {p.key: "42" for p in prompts},
    )


def test_a_report_is_filed_under_its_own_family_when_two_share_an_id(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T48. Two manifests, one id, two families: each report's facts land on its own key.

    The view keyed its session facts by `(acted_on.type, id)` and checked only
    the id against the report, because the report carried no family. So a report
    about the `mod` twin, delivered while the `module` twin was the press on
    record, filed its facts under the `module` twin -- a chip on the wrong row.

    Now the report's own family is the key, whatever is on record; the one thing
    still gated on the press on record is `forget()`, which deletes a file.

    Mutation: key `_note_session_facts()` by `acted_on.type` and a mismatched
    press files its rebuild against `("module", "twin")`; match `_module_done()`'s
    `forget()` on the id alone and the `module` twin's record is dropped; let an
    unverified report fall through to the clearing path and the seeded facts go.
    """
    # Both on disk: `_module_done()` reloads, and a reload drops every fact about
    # a module that is not installed.
    view = _wotlk_modules_view(ps, tmp_path, module=frozenset({"twin"}), mod=frozenset({"twin"}))
    module_twin, mod_twin = (
        parse_manifest(
            {
                "id": "twin",
                "name": "Twin",
                "type": kind,
                "game": "wow-wotlk",
                "description": "x",
                "source": {"repo": "acme/twin"},
            }
        )
        for kind in ("module", "mod")
    )

    view._acting_on = module_twin
    view._module_done(ApplyReport("install", "twin", family="module", rebuild_required=True))
    assert view._rebuild_owed == {("module", "twin")}

    pending = (apply_module.PendingSql("world", "data/sql/*.sql", ("one.sql",)),)
    view._acting_on = mod_twin
    view._module_done(ApplyReport("install", "twin", family="mod", pending_sql=pending))
    assert set(view._sql_owed) == {("mod", "twin")}
    assert view._rebuild_owed == {("module", "twin")}

    # And the record `forget()` drops on a remove is matched the same way: a `mod`
    # report must not drop the `module` twin's record.
    forgotten: list[tuple[str, str]] = []
    object.__setattr__(
        view.services,
        "module_forget",
        lambda manifest: forgotten.append((manifest.type, manifest.id)) or True,
    )
    view._acting_on = module_twin
    view._module_done(ApplyReport("remove", "twin", family="mod"))
    assert forgotten == []
    view._acting_on = mod_twin
    view._module_done(ApplyReport("remove", "twin", family="mod"))
    assert forgotten == [("mod", "twin")]

    # A report whose family is not the press on record -- or with no press on
    # record at all -- is filed under ITS OWN family, never the one on record,
    # and never dropped: a rebuild the user owes must not vanish over bookkeeping.
    view._rebuild_owed.clear()
    view._acting_on = module_twin
    view._module_done(ApplyReport("install", "twin", family="mod", rebuild_required=True))
    assert view._rebuild_owed == {("mod", "twin")}, view._rebuild_owed
    view._rebuild_owed.clear()
    view._acting_on = None
    view._module_done(ApplyReport("install", "twin", family="module", rebuild_required=True))
    assert view._rebuild_owed == {("module", "twin")}, view._rebuild_owed

    # But an unverified report never CLEARS (review round 2). Seed every fact on
    # the `mod` twin, then deliver a `mod` remove and an empty `mod` install while
    # the `module` twin is on record: all three facts survive both.
    mod_key = ("mod", "twin")
    view._rebuild_owed.add(mod_key)
    view._sql_owed[mod_key] = ("one.sql",)
    view._behind[mod_key] = 3
    for report in (
        ApplyReport("remove", "twin", family="mod"),
        ApplyReport("install", "twin", family="mod"),
    ):
        view._acting_on = module_twin
        view._module_done(report)
        assert mod_key in view._rebuild_owed, report
        assert view._sql_owed.get(mod_key) == ("one.sql",), report
        assert view._behind.get(mod_key) == 3, report


def test_an_install_that_needs_a_rebuild_raises_the_banner_and_the_chip(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The sentence `_format_report` has always printed, now attached to a control.

    The banner names WHICH module owes it, because the next question after "a
    rebuild is owed" is "for what?" — and the chip puts the same fact on the row
    that owes it, where a user scrolling the card will meet it.

    Mutation: drop the `_note_session_facts` call from `_module_done` and both
    the banner and the chip stay away while the report says a rebuild is owed.
    """
    view = _wotlk_modules_view(ps, tmp_path, module=frozenset({"mod-solocraft"}))
    assert view.rebuild_banner.isHidden() is True

    _deliver_report(
        view, ApplyReport("install", "mod-solocraft", family="module", rebuild_required=True)
    )

    assert view.rebuild_banner.isHidden() is False
    assert "mod-solocraft" in view.rebuild_banner_label.text()
    labels = [b.text() for b in view.modules_panel.row("mod-solocraft").chip_buttons]
    assert modules_panel.CHIP_REBUILD_PENDING in labels, labels


def _owing_a_rebuild(
    ps: _Ps, tmp_path: Path, services: ControllerServices | None = None
) -> ControllerView:
    """A view whose `mod-solocraft` install has just reported `rebuild_required`."""
    services = services or _services(ps, tmp_path, [])
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-solocraft"})}
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    _deliver_report(
        view, ApplyReport("install", "mod-solocraft", family="module", rebuild_required=True)
    )
    assert view.rebuild_banner.isHidden() is False, "the ground for every assertion below"
    return view


def test_only_a_compile_that_succeeds_clears_the_banner_and_the_chip(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven through `rebuild_server()`, not by calling the slot -- the slot is shared.

    Three actions run through `rebuild_log` (the rebuild, the database updates
    and the adopt) and all three reach `_rebuild_finished`, so "the panel said
    ok" is not "the server was compiled". And a rebuild that FAILS owes the same
    rebuild it did before, because nothing about the running server changed.

    Mutation: clear `_rebuild_owed` on `ok` alone, or on `_rebuild_finished`
    being reached at all, and one of the two halves below goes green while the
    tab tells a user their module is live.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: controller_view_module.QMessageBox.StandardButton.Yes,
    )

    def fails(cancel: object = None) -> Iterator[str]:
        yield "configuring"
        raise InstallerError("the build failed (exit 1)")

    def works(cancel: object = None) -> Iterator[str]:
        yield "built"

    services = _services(ps, tmp_path, [])
    services.rebuild = fails
    view = _owing_a_rebuild(ps, tmp_path, services)

    assert view.rebuild_server() is True
    pump_until(
        lambda: not view.rebuild_log.running and not view._busy, "the failed rebuild finished"
    )
    assert view.rebuild_banner.isHidden() is False, "a failed rebuild owes the same rebuild"

    services.rebuild = works
    assert view.rebuild_server() is True
    pump_until(lambda: not view.rebuild_log.running and not view._busy, "the rebuild finished")

    assert view.rebuild_banner.isHidden() is True
    assert view.modules_panel.row("mod-solocraft").chip_buttons == ()


def test_a_database_update_through_the_same_panel_clears_no_rebuild(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other two presses on that panel compile nothing, and must say nothing about it.

    `rebuild_log` is deliberately ONE panel for every long job on this tab, so
    `_rebuild_finished(True, ...)` arrives for a database-updates run and for an
    adopt as readily as for a compile. A user who applied three SQL files would
    otherwise watch the rebuild banner disappear and read that as "my module is
    in the server now".

    Mutation: drop `_rebuild_is_compile` and clear on `ok` alone -- this test is
    the only one that sees it, because the compile path is green either way.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    services, started, _asked = _updates_services(ps, tmp_path)
    view = _owing_a_rebuild(ps, tmp_path, services)

    assert view.apply_database_updates() is True
    pump_until(
        lambda: not view.rebuild_log.running and not view._busy,
        "the database updates finished",
    )

    assert started, "the route really ran"
    assert view.rebuild_banner.isHidden() is False
    labels = [b.text() for b in view.modules_panel.row("mod-solocraft").chip_buttons]
    assert modules_panel.CHIP_REBUILD_PENDING in labels, labels


def test_a_report_with_pending_sql_puts_the_files_on_the_chip(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """An owed chip's press writes its own detail into the report box.

    Mutation: name the chip and not the files (`detail = label`) and the press
    tells the reader nothing they could not see on the chip.
    """
    view = _wotlk_modules_view(ps, tmp_path, module=frozenset({"mod-transmog"}))
    _deliver_report(
        view,
        ApplyReport(
            "install",
            "mod-transmog",
            family="module",
            pending_sql=(
                apply_module.PendingSql("world", "data/sql/db-world/*.sql", ("one.sql",)),
            ),
        ),
    )

    chips = {b.text(): b for b in view.modules_panel.row("mod-transmog").chip_buttons}
    assert modules_panel.CHIP_SQL_PENDING in chips, list(chips)
    chips[modules_panel.CHIP_SQL_PENDING].click()
    assert "one.sql" in view.module_report.toPlainText()


def test_the_importer_finishing_clears_every_sql_chip(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """One run covers every module on this install, which is the granularity it has.

    Mutation: leave `_sql_owed` alone in `_module_sql_done` and the chip stands
    after the very press it asks for.
    """
    view = _wotlk_modules_view(ps, tmp_path, module=frozenset({"mod-transmog"}))
    _deliver_report(
        view,
        ApplyReport(
            "install",
            "mod-transmog",
            family="module",
            pending_sql=(
                apply_module.PendingSql("world", "data/sql/db-world/*.sql", ("one.sql",)),
            ),
        ),
    )
    assert view.modules_panel.row("mod-transmog").chip_buttons

    view._module_sql_done(docker.AttachedRun(0, ()))

    assert view.modules_panel.row("mod-transmog").chip_buttons == ()


def test_an_update_check_puts_the_count_on_the_row_it_counted(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """8.7a's figure, on the row rather than only in a block of text.

    The report keeps `ModuleUpdate.line` verbatim — 8.7a's definition of done is
    that the number equals `git rev-list --count HEAD..FETCH_HEAD` run by hand,
    and the view must not re-format it — so the chip carries the same integer
    and nothing derived.

    Mutation: keep `behind=None` ("could not ask") as a zero or as a chip, and a
    checkout git never answered for is reported as up to date or as behind by
    nothing.
    """
    view = _wotlk_modules_view(ps, tmp_path, module=frozenset({"mod-transmog", "mod-aoe-loot"}))
    view._module_updates_done(
        (
            apply_module.ModuleUpdate("mod-transmog", tmp_path, True, 3),
            apply_module.ModuleUpdate("mod-aoe-loot", tmp_path, True, None),
        )
    )

    assert "3 commits behind" in view.module_report.toPlainText()
    labels = [b.text() for b in view.modules_panel.row("mod-transmog").chip_buttons]
    assert modules_panel.chip_update_label(3) in labels, labels
    assert view.modules_panel.row("mod-aoe-loot").chip_buttons == ()


def test_busy_greys_every_row_button_and_gives_them_back(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The gate that used to grey two toolbar buttons now greys every row's own.

    Mutation: drop the `set_enabled_actions(False)` from `_set_busy` and a press
    during a rebuild reaches the applier while the compile is reading the very
    folder it clones into.
    """
    view = _wotlk_modules_view(ps, tmp_path, module=frozenset({"mod-transmog"}))

    def presses() -> list[bool]:
        # Rows the ROW's own answer keeps disabled are left out: the busy gate
        # never overrides that (`set_enabled_actions`), so a locked Install --
        # eleven of them here, one per shipped `requires` with nothing on disk
        # to satisfy it (T69) -- would read as the gate still being on.
        return [
            (row.install_button or row.remove_button).isEnabled()
            for row in view.modules_panel.rows()
            if (row.install_button is not None and row.data.installable)
            or (row.remove_button is not None and row.data.removable)
        ]

    assert presses() and all(presses())
    view._set_busy(True)
    assert not any(presses())
    view._set_busy(False)
    assert all(presses())


def test_a_game_with_no_applier_has_no_live_row_button(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The `store is None or applier is None` gate, moved onto the rows.

    Mutation: call `set_enabled_actions(True)` at build time and a game with no
    applier offers a press that returns at `_module_action`'s second line.
    """
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "applier", None)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    live = [
        row.data.id
        for row in view.modules_panel.rows()
        if (row.install_button is not None and row.install_button.isEnabled())
        or (row.remove_button is not None and row.remove_button.isEnabled())
    ]
    assert live == [], live


def test_a_rows_install_button_installs_that_row(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The press that replaced "Install selected", through the one shared handler.

    Mutation: connect `install_pressed` straight to `applier.install` and the
    prompt dialog, the cancel path and the report are all skipped.
    """
    view = _wotlk_modules_view(ps, tmp_path)

    view.modules_panel.row("mod-aoe-loot").install_button.click()

    applier = view.services.applier
    assert isinstance(applier, _FakeApplier) and applier.installed == ["mod-aoe-loot"]
    assert view.modules_panel.selected_id() == "mod-aoe-loot"


# ------------------------------------------------------ T42 round 2 (Codex review)


def test_a_stopped_rebuild_keeps_the_debt_it_started_with(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LogPanel` reports a STOPPED job as ok=True, and it is right to.

    Its worker's `except` branch turns the terminated child's non-zero exit into
    `ok=True, message="stopped"` on purpose -- reporting a refusal for a button
    the user pressed is the bug that branch exists to fix. So `ok` alone cannot
    mean "the server was compiled", and reading this clause's success off `ok`
    emptied the banner on a Stop: the module is still not in the running server.

    `LogPanel.cancelled` is the property that says so, and `catalog_view.
    _on_run_finished()` already reads it. The MESSAGE is deliberately not read:
    grepping for the word "stopped" would be the same defect in a new place.

    Mutation: drop `and not self.rebuild_log.cancelled` and the banner vanishes
    on a Stop.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    reached = threading.Event()
    release = threading.Event()

    def blocks(cancel: object = None) -> Iterator[str]:
        yield "configuring"
        reached.set()
        release.wait(HANG_BOUND)
        yield "never gets here"

    services = _services(ps, tmp_path, [])
    services.rebuild = blocks
    view = _owing_a_rebuild(ps, tmp_path, services)

    assert view.rebuild_server() is True
    pump_until(reached.is_set, "the rebuild reached its first line")
    view.rebuild_log.stop()
    release.set()
    pump_until(
        lambda: not view.rebuild_log.running and not view._busy, "the stopped rebuild finished"
    )

    assert view.rebuild_log.cancelled is True, "the panel knows it was stopped"
    assert view.rebuild_banner.isHidden() is False, "a stop did not compile anything"
    labels = [b.text() for b in view.modules_panel.row("mod-solocraft").chip_buttons]
    assert modules_panel.CHIP_REBUILD_PENDING in labels, labels


def test_an_adopt_through_the_same_panel_clears_no_rebuild(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third press on `rebuild_log`, and the one the round-1 tests never covered.

    It starts a database and writes one row. It compiles nothing, so it must say
    nothing about whether a module is in the server.

    Mutation: set `_rebuild_is_compile = True` in `adopt_as_imported()` (or drop
    the flag) and the banner goes away on a press that touched no binary.
    """
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    services, started, _asked, _probed = _adopt_services(ps, tmp_path)
    view = _owing_a_rebuild(ps, tmp_path, services)

    assert view.adopt_as_imported() is True
    pump_until(lambda: not view.rebuild_log.running and not view._busy, "the adopt finished")

    assert started, "the route really ran"
    assert view.rebuild_banner.isHidden() is False
    labels = [b.text() for b in view.modules_panel.row("mod-solocraft").chip_buttons]
    assert modules_panel.CHIP_REBUILD_PENDING in labels, labels


def test_a_successful_removal_forgets_everything_owed_about_that_module(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """All three containers, because each would go on describing something that is gone.

    An update count is about a checkout that no longer exists; a pending-SQL
    list names files nothing will read; and the banner would name a module that
    is not installed.

    Mutation: drop any one of the three lines in `_note_session_facts`'s remove
    branch and that one assertion fails.
    """
    view = _wotlk_modules_view(ps, tmp_path, module=frozenset({"mod-transmog"}))
    _deliver_report(
        view,
        ApplyReport(
            "install",
            "mod-transmog",
            family="module",
            rebuild_required=True,
            pending_sql=(
                apply_module.PendingSql("world", "data/sql/db-world/*.sql", ("one.sql",)),
            ),
        ),
    )
    view._module_updates_done((apply_module.ModuleUpdate("mod-transmog", tmp_path, True, 2),))
    assert view._rebuild_owed and view._sql_owed and view._behind

    _deliver_report(view, ApplyReport("remove", "mod-transmog", family="module"))

    assert view._rebuild_owed == set()
    assert view._sql_owed == {}
    assert view._behind == {}
    assert view.rebuild_banner.isHidden() is True


def test_a_clone_deleted_outside_the_app_takes_its_chips_and_the_banner_with_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A folder can leave without this app pressing anything.

    Until round 2 the reload drew the catalog row as `Not installed` with a
    `Rebuild pending` chip hanging off it, and went on naming the module in the
    banner. Reconciled in `reload_modules()` rather than by gating the chips,
    because the banner is built from `_rebuild_owed` and not from the rows -- a
    chip-only gate would leave the banner claiming a module that is gone.

    Mutation: drop `_forget_what_is_no_longer_installed()` and the chip and the
    banner both survive the deletion.
    """
    on_disk = {"module": frozenset({"mod-transmog"})}
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "installed_modules", lambda: dict(on_disk))
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    _deliver_report(
        view, ApplyReport("install", "mod-transmog", family="module", rebuild_required=True)
    )
    assert view.rebuild_banner.isHidden() is False

    on_disk["module"] = frozenset()  # somebody deleted modules/mod-transmog
    view.reload_modules()

    assert view.modules_panel.row("mod-transmog").badge_label.text() == BADGE_NOT_INSTALLED
    assert view.modules_panel.row("mod-transmog").chip_buttons == ()
    assert view.rebuild_banner.isHidden() is True


def test_a_game_with_no_installed_reader_keeps_what_this_session_learned(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """ "No reader" is not evidence that nothing is installed.

    A game whose `installed_modules` seam is `None` answers `{}` for every
    family, and reconciling against that would throw away every fact the session
    has learned on the first reload after an install.

    Mutation: reconcile unconditionally (drop the `if reader is not None`) and
    the banner is empty one line after the install that raised it.
    """
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "installed_modules", None)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    _deliver_report(
        view, ApplyReport("install", "mod-transmog", family="module", rebuild_required=True)
    )

    assert view.rebuild_banner.isHidden() is False
    assert "mod-transmog" in view.rebuild_banner_label.text()


def _row_menu(view: ControllerView, module_id: str) -> object:
    """The row's real context menu, built the way a right-click builds it.

    `QMenu.exec` cannot be replaced from Python -- it is a Shiboken slot, and an
    assignment to it is accepted and then ignored (measured, round 2: a test
    that patched it sat on a real popup until it was killed). So the SHOWING and
    the BUILDING are separate methods, and this drives the builder. What the
    right-click itself adds -- selecting the row and handing the id over -- is
    asserted by `test_a_right_click_selects_the_row_and_builds_its_own_menu`.
    """
    view.modules_panel.select(module_id)
    return view._module_menu(module_id)


def test_a_right_click_selects_the_row_and_builds_its_own_menu(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two lines `_row_menu()` cannot reach, driven through the real entry point.

    The menu is stubbed to an EMPTY `QMenu`, whose `exec` returns at once
    (measured) -- so the real `_show_module_context_menu` runs end to end, in
    the order it really runs in, without a popup to hang on.

    Mutation: drop the `select()` and the menu is built for a row the tab has
    not selected, which is what every entry on it then acts against.
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu

    view = _wotlk_modules_view(ps, tmp_path)
    asked: list[str] = []
    monkeypatch.setattr(
        ControllerView,
        "_module_menu",
        lambda self, module_id: (asked.append(module_id), QMenu(self))[1],
    )

    view._show_module_context_menu("mod-aoe-loot", QPoint(0, 0))

    assert asked == ["mod-aoe-loot"]
    assert view.modules_panel.selected_id() == "mod-aoe-loot"


def test_the_menu_on_an_uncatalogued_row_offers_the_answer_and_not_two_dead_actions(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Round 2, Codex: the menu offered Install and Remove where there is no manifest.

    Both ended at `UNCATALOGUED_PRESS`, so nothing was done to the machine --
    but two entries that name an action and then explain that the action does
    not exist are two controls that look live and are not, which is the reading
    this whole ticket exists to remove.

    Mutation: gate the branch on `_module_actions_allowed()` alone (round 1's
    shape) and the menu carries "Install Selected Module" again.
    """
    from yulon.ui.controller_view import UNCATALOGUED_PRESS, WHY_UNCATALOGUED

    view = _installed_view(ps, tmp_path, module=frozenset({"mod-something-homemade"}))
    menu = _row_menu(view, "mod-something-homemade")

    labels = [action.text() for action in menu.actions() if action.text()]
    assert labels == [WHY_UNCATALOGUED, "Copy Module ID"], labels

    next(a for a in menu.actions() if a.text() == WHY_UNCATALOGUED).trigger()
    assert view.module_report.toPlainText() == UNCATALOGUED_PRESS


def test_the_menu_on_a_catalogued_row_still_installs_and_removes(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The other branch, which round 1 left untested altogether.

    Mutation: leave the Install entry connected to `_module_action("remove")`
    (or drop the `elif`) and the press does the other thing, or nothing.
    """
    view = _wotlk_modules_view(ps, tmp_path)
    menu = _row_menu(view, "mod-aoe-loot")

    labels = [action.text() for action in menu.actions() if action.text()]
    assert labels == ["Install Selected Module", "Remove Selected Module", "Copy Module ID"]

    next(a for a in menu.actions() if a.text() == "Install Selected Module").trigger()
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier) and applier.installed == ["mod-aoe-loot"]


def test_the_selected_manifest_of_a_shared_id_is_the_one_whose_row_is_selected(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Round 2, Codex: `_manifests` was id-keyed, so this handed over the wrong steps.

    The object this returns goes straight to `applier.install()`. On an id two
    families share, a bare-id lookup installs the other family's steps -- a
    `mod`'s SQL where a `module`'s clone was asked for.

    The collision is constructed rather than found: the shipped catalog has no
    such id today (Codex checked, and so did this hand), which is precisely why
    the defect was invisible. BOTH directions are asserted, because an id-keyed
    dict is last-write-wins and would answer one of them correctly by accident --
    `FAMILY_FILES` loads `mod` after `module`, so the `mod` row's answer was
    right and the `module` row's was the other family's steps. The `mod` row is
    reached through a real CLICK, because a bare id resolves past it by design.

    Mutation: look the manifest up by id alone, newest entry first (which is
    what `_manifests[manifest.id] = manifest` gives), and the `module` row
    answers with the `mod` manifest -- the object handed to `applier.install()`.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    services = _services(ps, tmp_path, [])
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    twin = [m for m in view.modules_panel.rows() if m.data.family == "mod"][
        0
    ].data.id  # any shipped `mod`
    # Two manifests, one id, two families -- handed to the panel directly,
    # because the shipped catalog has no such collision and inventing one in the
    # store would be testing the store.
    module_twin = parse_manifest(
        {
            "schema_version": 1,
            "id": twin,
            "name": "The module one",
            "type": "module",
            "game": "wow-wotlk",
            "source": {"repo": f"acme/{twin}"},
        }
    )
    mod_twin = view._manifests[("mod", twin)]
    view._manifests[("module", twin)] = module_twin
    view.modules_panel.set_rows(
        modules_panel.build_module_rows(
            [module_twin, mod_twin], {}, modules_panel.SessionState(), None
        )
    )

    view.modules_panel.resize(600, 800)
    view.modules_panel.show()

    view.modules_panel.select(twin)  # the bare id resolves to `module`, the first family
    picked = view.selected_manifest()
    assert picked is not None and picked.type == "module", picked

    chosen = [r for r in view.modules_panel.rows() if r.data.family == "mod"][0]
    QTest.mouseClick(chosen.description_label, Qt.MouseButton.LeftButton)
    picked = view.selected_manifest()
    assert picked is not None and picked.type == "mod", picked
    view.modules_panel.hide()


# -- T43: the Tuning tab ----------------------------------------------------


def _deploy(server_dir: Path, rel: str, text: str) -> Path:
    path = server_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


TRANSMOG_CONF = "env/dist/etc/modules/transmog.conf"


def _tuned_view(ps: _Ps, tmp_path: Path) -> ControllerView:
    """A WotLK view with `mod-transmog` installed and its conf deployed.

    The same module the live install on `yulon-win11` reported five unwritten
    keys for on 2026-09-12, which is the report this whole ticket came from.
    """
    _deploy(
        tmp_path,
        TRANSMOG_CONF,
        "[worldserver]\n"
        "#\n"
        "Transmogrification.Enable = 1\n"
        "Transmogrification.ShowSetDisclaimer = 1\n",
    )
    return _installed_view(ps, tmp_path, module=frozenset({"mod-transmog"}))


def test_the_tuning_tab_lists_the_settings_of_installed_modules_only(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuned_view(ps, tmp_path)
    cards = [card.card.module_id for card in view.tuning_panel.cards()]
    assert cards == ["mod-transmog"], cards
    keys = list(view.tuning_panel.card("mod-transmog").editors)
    assert "Transmogrification.Enable" in keys and len(keys) == 5


def test_a_setting_shows_what_the_deployed_conf_says_and_never_invents_one(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The five keys carry no `default`, so four of them have no value at all."""
    view = _tuned_view(ps, tmp_path)
    editors = view.tuning_panel.card("mod-transmog").editors
    here = editors["Transmogrification.Enable"]
    assert here.control.isChecked() and here.value() == "1"
    assert here.note_label is None
    missing = editors["Transmogrification.UseCollectionSystem"]
    # A switch cannot draw "no value": it is off, and it says WHY it is off
    # rather than passing the unchecked box off as a reading of the file.
    assert not missing.control.isChecked()
    assert not missing.changed, "an untouched switch reported a change nobody made"
    assert missing.note_label is not None


def test_a_game_with_nothing_installed_says_so_instead_of_an_empty_tab(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.tuning_panel.cards() == ()
    assert not view.tuning_panel.empty_label.isHidden()


def test_saving_a_card_writes_what_changed_and_names_the_backup(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuned_view(ps, tmp_path)
    path = tmp_path / TRANSMOG_CONF
    before = path.read_text(encoding="utf-8")
    card = view.tuning_panel.card("mod-transmog")
    card.editors["Transmogrification.Enable"].control.setChecked(False)
    assert card.save_button is not None
    card.save_button.click()

    after = path.read_text(encoding="utf-8")
    assert "Transmogrification.Enable = 0" in after
    assert "Transmogrification.ShowSetDisclaimer = 1" in after, "an untouched key moved"
    (backup,) = tuning.backups_of(path)
    assert backup.read_text(encoding="utf-8") == before
    said = view.tuning_report.toPlainText()
    assert "Transmogrification.Enable" in said and backup.name in said
    # And the cards were re-read off the file afterwards: the row that was just
    # written is no longer marked as changed, because the file now says so.
    redrawn = view.tuning_panel.card("mod-transmog").editors["Transmogrification.Enable"]
    assert redrawn.value() == "0" and not redrawn.control.isChecked()
    assert not redrawn.changed


def test_a_save_that_changed_nothing_writes_nothing_and_says_so(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuned_view(ps, tmp_path)
    path = tmp_path / TRANSMOG_CONF
    before = path.read_text(encoding="utf-8")
    card = view.tuning_panel.card("mod-transmog")
    assert card.save_button is not None
    card.save_button.click()
    assert path.read_text(encoding="utf-8") == before
    assert tuning.backups_of(path) == ()
    # The BEHAVIOUR, not the wording: the report is the one written for a save
    # with nothing in it, and it names the module it is about.
    assert view.tuning_report.toPlainText() == (
        controller_view_module.TUNING_NOTHING_CHANGED.format(module="mod-transmog")
    )


def test_a_key_the_conf_never_carried_is_written_for_the_first_time(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The whole point of the ticket: 73 of the 107 keys are unreachable today."""
    view = _tuned_view(ps, tmp_path)
    card = view.tuning_panel.card("mod-transmog")
    card.editors["Transmogrification.UseCollectionSystem"].control.setChecked(True)
    assert card.save_button is not None
    card.save_button.click()
    assert "Transmogrification.UseCollectionSystem = 1" in (tmp_path / TRANSMOG_CONF).read_text(
        encoding="utf-8"
    )


def test_a_value_that_fails_its_type_is_refused_and_the_file_is_untouched(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _tuned_view(ps, tmp_path)
    path = tmp_path / TRANSMOG_CONF
    before = path.read_text(encoding="utf-8")
    card = view.tuning_panel.card("mod-transmog")
    key = "Transmogrification.Enable"
    # The shipped manifest types this key `bool`, and a switch cannot produce a
    # value that fails its own check. The refusal is asked for by handing
    # `tuning.check()` a declaration the switch's `0` cannot satisfy -- the spec
    # is read at SAVE time, and the control was drawn before it.
    monkeypatch.setattr(
        view,
        "_tuning_spec",
        lambda family, module_id, file: {key: ConfKey(key=key, type="int", min=5, max=9)},
    )
    card.editors[key].control.setChecked(False)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    assert card.save_button is not None
    card.save_button.click()
    assert path.read_text(encoding="utf-8") == before
    assert tuning.backups_of(path) == ()
    assert key in view.tuning_report.toPlainText()
    assert failures and key in failures[0]


def test_revert_puts_the_conf_back_from_the_backup(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = _tuned_view(ps, tmp_path)
    path = tmp_path / TRANSMOG_CONF
    before = path.read_text(encoding="utf-8")
    card = view.tuning_panel.card("mod-transmog")
    card.editors["Transmogrification.Enable"].control.setChecked(False)
    assert card.save_button is not None and card.revert_button is not None
    card.save_button.click()
    assert path.read_text(encoding="utf-8") != before
    view.tuning_panel.card("mod-transmog").revert_button.click()
    assert path.read_text(encoding="utf-8") == before


def test_a_revert_with_no_backup_says_so_rather_than_doing_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuned_view(ps, tmp_path)
    card = view.tuning_panel.card("mod-transmog")
    assert card.revert_button is not None
    card.revert_button.click()
    assert view.tuning_report.toPlainText() == controller_view_module.TUNING_NO_BACKUP.format(
        module="mod-transmog", file=TRANSMOG_CONF
    )


def test_the_file_picker_lists_the_deployed_confs_and_opens_the_first(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuned_view(ps, tmp_path)
    # T44 item 13: the picker is a row of buttons, and each one's tooltip is
    # the FILE it stands for -- the label is a basename and is not the identity.
    listed = [b.toolTip() for b in view.tuning_panel.file_buttons()]
    assert listed == [TRANSMOG_CONF], listed
    assert "Transmogrification.Enable = 1" in view.tuning_panel.editor.toPlainText()
    assert not view.tuning_panel.editor.isReadOnly()


def test_the_servers_own_conf_is_listed_read_only_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T43's own follow-up: who owns core configuration is a bigger question."""
    _deploy(tmp_path, "env/dist/etc/worldserver.conf", "[worldserver]\nMotd = hi\n")
    view = _tuned_view(ps, tmp_path)
    core = next(
        b
        for b in view.tuning_panel.file_buttons()
        if b.toolTip() == "env/dist/etc/worldserver.conf"
    )
    assert core.text().endswith(tuning_panel.READ_ONLY_SUFFIX)
    core.click()
    assert view.tuning_panel.editor.isReadOnly()
    assert view.tuning_panel.file_note.text() == controller_view_module.TUNING_CORE_FILE
    assert not view.tuning_panel.file_save_button.isEnabled()


def test_a_raw_save_that_still_looks_like_a_conf_asks_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: asked.append("asked")
        or int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    view = _tuned_view(ps, tmp_path)
    view.tuning_panel.editor.setPlainText("[worldserver]\nTransmogrification.Enable = 0\n")
    view.tuning_panel.file_save_button.click()
    assert asked == []
    assert (tmp_path / TRANSMOG_CONF).read_text(encoding="utf-8") == (
        "[worldserver]\nTransmogrification.Enable = 0\n"
    )


def test_a_raw_save_that_stopped_looking_like_a_conf_asks_once_and_a_no_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard warns and gates; it never blocks (T43 point 4)."""
    view = _tuned_view(ps, tmp_path)
    path = tmp_path / TRANSMOG_CONF
    before = path.read_text(encoding="utf-8")
    said: list[str] = []

    def refuse(parent: object, title: str, text: str, *rest: object) -> int:
        said.append(text)
        return int(controller_view_module.QMessageBox.StandardButton.No)

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", refuse)
    view.tuning_panel.editor.setPlainText("this is not a setting\n")
    view.tuning_panel.file_save_button.click()
    # The confirm is `tuning.lint_sentence()`'s own, not a sentence this test
    # spells a second time: what matters is that the guard ran on the text in
    # the box and that its verdict is what the user was shown.
    assert said == [tuning.lint_sentence(tuning.lint("this is not a setting\n"))]
    assert path.read_text(encoding="utf-8") == before
    assert tuning.backups_of(path) == ()

    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: int(controller_view_module.QMessageBox.StandardButton.Yes),
    )
    view.tuning_panel.file_save_button.click()
    assert path.read_text(encoding="utf-8") == "this is not a setting\n"


def test_busy_greys_the_tuning_saves_and_gives_them_back(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuned_view(ps, tmp_path)
    card = view.tuning_panel.card("mod-transmog")
    assert card.save_button is not None
    view._set_busy(True)
    assert not card.save_button.isEnabled()
    view._set_busy(False)
    assert card.save_button.isEnabled()


def test_a_card_says_what_applying_its_change_costs(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Computed, not typed into the view: the same sentence the raw editor shows."""
    view = _tuned_view(ps, tmp_path)
    card = view.tuning_panel.card("mod-transmog")
    assert card.rule_label.text() == tuning.apply_sentence("restart")
    assert view.tuning_panel.file_note.text() == tuning.apply_sentence("restart")


def test_a_multi_file_card_writes_nothing_when_the_second_files_value_is_bad(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Refused before a byte is written" has to hold over the CARD, not per file.

    NPC Beastmaster is the shipped module with two: four keys in its own conf
    and `Creatures.CustomIDs` in the core's `worldserver.conf`. Validating and
    writing one file at a time landed the first file's change and only then
    refused the second, which is the half-applied state the guarantee exists to
    prevent.
    """
    own = "env/dist/etc/modules/mod_npc_beastmaster.conf"
    core = "env/dist/etc/worldserver.conf"
    _deploy(tmp_path, own, "[worldserver]\nBeastMaster.Enable = 1\n")
    _deploy(tmp_path, core, '[worldserver]\nCreatures.CustomIDs = "1,2"\n')
    view = _installed_view(ps, tmp_path, module=frozenset({"mod-npc-beastmaster"}))
    before = (tmp_path / own).read_bytes()
    card = view.tuning_panel.card("mod-npc-beastmaster")
    # The module's own conf comes first in the manifest, so its write is the one
    # that would already have landed.
    card.editors["BeastMaster.Enable"].control.setChecked(False)
    card.editors["Creatures.CustomIDs"].control.setText("not a number")
    monkeypatch.setattr(
        view,
        "_tuning_spec",
        lambda module_id, file: (
            {"Creatures.CustomIDs": ConfKey(key="Creatures.CustomIDs", type="int")}
            if file == core
            else {}
        ),
    )
    assert card.save_button is not None
    card.save_button.click()
    assert (tmp_path / own).read_bytes() == before, "the first file was written anyway"
    assert tuning.backups_of(tmp_path / own) == ()
    assert tuning.backups_of(tmp_path / core) == ()


def test_a_conf_that_is_not_utf8_opens_empty_and_read_only(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """An editor holding U+FFFD is one Save away from writing that to disk."""
    view = _tuned_view(ps, tmp_path)
    path = tmp_path / TRANSMOG_CONF
    path.write_bytes(b"[worldserver]\n# \xff\nTransmogrification.Enable = 1\n")
    view.open_tuning_file(TRANSMOG_CONF)
    assert view.tuning_panel.editor.toPlainText() == ""
    assert view.tuning_panel.editor.isReadOnly()
    assert not view.tuning_panel.file_save_button.isEnabled()
    assert TRANSMOG_CONF in view.tuning_panel.file_note.text()


def test_a_saved_card_of_a_shared_id_writes_that_familys_file_and_no_other(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T42 round 2's collision, on the Tuning tab, end to end through the view.

    The shipped `wow-wotlk` catalog has no id in two families, so the panel
    tests build the twin from synthetic rows and this one builds it the way
    `test_the_selected_manifest_of_a_shared_id_is_the_one_whose_row_is_selected`
    does: two real manifests under one id, handed to the panel directly.
    """
    mine = "env/dist/etc/modules/mine.conf"
    theirs = "env/dist/etc/modules/theirs.conf"
    shared = "env/dist/etc/worldserver.conf"
    _deploy(tmp_path, mine, "[worldserver]\nK = 1\n")
    _deploy(tmp_path, theirs, "[worldserver]\nK = 1\n")
    _deploy(tmp_path, shared, "[worldserver]\nS = 1\n")
    view = _tuned_view(ps, tmp_path)
    twins = [
        parse_manifest(
            {
                "schema_version": 1,
                "id": "twin",
                "name": "Twin",
                "type": family,
                "game": "wow-wotlk",
                "description": "two families, one id",
                "source": {"repo": "acme/twin"},
                **({"sparse_path": "x"} if False else {}),
                # DIFFERENT declarations for the same key name, so reading the
                # wrong family's manifest is visible: `9` is a fine `int` and
                # not an on/off value, so a spec taken from the `module` twin
                # refuses the `mod` twin's save.
                "conf": [
                    {"file": file, "keys": [{"key": "K", "type": kind}]},
                    # And a key both families declare in the SAME file with
                    # DIFFERENT types, which is the only shape that can catch a
                    # spec read from the wrong family: `9` is a fine `int` and
                    # not an on/off value.
                    {"file": shared, "keys": [{"key": "S", "type": kind}]},
                ],
            }
        )
        for family, file, kind in (("module", mine, "bool"), ("mod", theirs, "int"))
    ]
    for manifest in twins:
        view._manifests[(manifest.type, manifest.id)] = manifest
    rows = tuning.rows_for(
        twins, {"module": frozenset({"twin"}), "mod": frozenset({"twin"})}, tmp_path
    )
    view.tuning_panel.set_cards(tuning_panel.build_tuning_cards(rows))

    card = view.tuning_panel.card(("mod", "twin"))
    assert card.card.files == (theirs, shared), card.card.files
    card.editors["K"].control.setText("9")
    card.editors["S"].control.setText("9")
    assert card.save_button is not None
    card.save_button.click()

    assert (tmp_path / theirs).read_text(encoding="utf-8") == "[worldserver]\nK = 9\n"
    assert (tmp_path / mine).read_text(encoding="utf-8") == "[worldserver]\nK = 1\n"
    assert (tmp_path / shared).read_text(encoding="utf-8") == "[worldserver]\nS = 9\n"
    assert "wrote" in view.tuning_report.toPlainText()


# ---------------------------------------------------- the version line (T44 item 1)


def test_the_first_paint_of_the_modules_tab_reads_no_clone_at_all(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Item 1's hard rule: building the tab must not cost a `git log` per module.

    A `git log -1` per installed row on every reload is what T41 refused and
    T42 restated, and `reload_modules()` runs after every install, every
    remove, every update check and every Refresh.

    Mutation: have `_known_versions()` call `VersionCache.fill()` instead of
    `known()` and the reader is asked once per installed module while the tab
    is still being laid out -- and every other test in this file stays green.
    """
    read: list[Path] = []
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-solocraft"})}
    )
    object.__setattr__(
        services, "module_version", lambda path: (read.append(path), "7c02b1d · 2026-09-01")[1]
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert read == [], "the tab was built by reading clones"
    assert view.modules_panel.row("mod-solocraft").version_label.text() == ""


def test_the_version_fills_in_after_the_tab_is_up_and_only_for_installed_rows(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A row that renders late is fine; a tab that takes a second per module is not.

    Mutation: drop the `_start_filling_versions()` call from `reload_modules()`
    and the line never arrives -- the tab looks exactly as it did before T44
    and the first test above still passes.
    """
    read: list[Path] = []
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-solocraft"})}
    )
    object.__setattr__(
        services, "module_version", lambda path: (read.append(path), "7c02b1d · 2026-09-01")[1]
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    pump_until(lambda: not view._filling_versions, "the version fill never finished")

    assert view.modules_panel.row("mod-solocraft").version_label.text() == "7c02b1d · 2026-09-01"
    assert read == [tmp_path / "modules" / "mod-solocraft"], "one read, and only the installed row"
    assert view.modules_panel.row("mod-transmog").version_label.text() == ""


def test_a_reload_after_the_fill_reads_nothing_again(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The cache is the point: the second reload costs no subprocess at all.

    Mutation: drop the `_known` write in `VersionCache.fill()` and every
    reload pays the reads again -- which is the cost item 1 exists to remove.
    """
    read: list[Path] = []
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-solocraft"})}
    )
    object.__setattr__(
        services, "module_version", lambda path: (read.append(path), "7c02b1d · 2026-09-01")[1]
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    pump_until(lambda: not view._filling_versions, "the first fill never finished")
    before = len(read)

    view.reload_modules()
    pump_until(lambda: not view._filling_versions, "the second fill never finished")

    assert len(read) == before
    assert view.modules_panel.row("mod-solocraft").version_label.text() == "7c02b1d · 2026-09-01"


def test_refresh_forgets_every_version_and_a_report_forgets_one(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The two invalidations, and they are different sizes on purpose.

    Refresh means "read the disk again" -- a clone can change under this app,
    through a `git pull` in a terminal. A report is about ONE module, and
    dropping the whole cache for it would re-read every other clone for
    nothing.

    Mutation: bind Refresh back to `reload_modules` and a module pulled in a
    terminal keeps showing the sha it had when the tab opened, forever.
    """
    read: list[Path] = []
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services,
        "installed_modules",
        lambda: {"module": frozenset({"mod-solocraft", "mod-transmog"})},
    )
    object.__setattr__(
        services, "module_version", lambda path: (read.append(path), "7c02b1d · 2026-09-01")[1]
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    pump_until(lambda: not view._filling_versions, "the first fill never finished")
    assert len(read) == 2

    _deliver_report(view, ApplyReport("install", "mod-solocraft", family="module"))
    pump_until(lambda: not view._filling_versions, "the fill after the report never finished")
    assert read[2:] == [tmp_path / "modules" / "mod-solocraft"], "one module, not both"

    view.refresh_modules_button.click()
    pump_until(lambda: not view._filling_versions, "the fill after Refresh never finished")

    assert len(read) == 5, "Refresh re-reads every installed clone"


def _update_view(ps: _Ps, tmp_path: Path, **seams: object) -> ControllerView:
    """A view whose applier is rooted at `tmp_path`, with a real clone on disk.

    The clone is real because `Applier.update()`'s first question is whether
    the folder is a checkout at all, and a fixture that skipped it would test
    a refusal the press never reaches.
    """
    clone = tmp_path / "modules" / "mod-solocraft"
    (clone / ".git").mkdir(parents=True)
    (clone / "mine.cpp").write_text("// three evenings\n", encoding="utf-8")
    applier = _FakeApplier(tmp_path, **seams)  # type: ignore[arg-type]
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "applier", applier)
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-solocraft"})}
    )
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts: {p.key: "42" for p in prompts},
    )
    view._behind = {("module", "mod-solocraft"): 3}
    view.reload_modules()
    return view


def _press_update(view: ControllerView) -> None:
    row = view.modules_panel.row("mod-solocraft")
    chip = next(b for b in row.chip_buttons if "Update available" in b.text())
    chip.click()
    assert row.detail_button is not None
    assert row.detail_button.text() == "Update"
    row.detail_button.click()
    pump_until(lambda: not view._busy, "the update never finished")


def test_the_update_press_runs_the_real_pull_over_a_clean_checkout(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T44 item 2, tested through `Applier.update()` rather than past it (round 2).

    The first version of this test used a fake applier that overrode
    `update()`, so it asserted only that the tab SELECTED the right method --
    and would have passed just as happily while the real path reset a user's
    work. The fake now overrides `install()` alone, so every refusal
    `update()` carries really runs here.

    Mutation: `update-press-routes-to-remove` -- route the press to
    `applier.remove` and the module the user asked to update is uninstalled.
    """
    solocraft = "https://github.com/azerothcore/mod-solocraft.git"
    view = _update_view(ps, tmp_path, origin=solocraft)

    _press_update(view)

    applier = view.services.applier
    assert isinstance(applier, _FakeApplier)
    assert applier.installed[-1] == "mod-solocraft"
    assert applier.removed == []


def test_the_update_press_refuses_a_checkout_with_work_in_it_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The whole of round 2's finding 1, at the control the user actually presses.

    The refusal is the applier's own sentence, in the report box every other
    answer on this tab is read in, and the file is still there.

    Mutation: `update-press-routes-to-install` -- send the press to
    `applier.install` again (round 1's routing) and the press resets the
    folder: `installed` grows and no refusal is shown.
    """
    solocraft = "https://github.com/azerothcore/mod-solocraft.git"
    view = _update_view(ps, tmp_path, origin=solocraft, unmodified=False)

    _press_update(view)

    applier = view.services.applier
    assert isinstance(applier, _FakeApplier)
    assert applier.installed == [], "the pull ran over uncommitted work"
    assert "has changes in it" in view.module_report.toPlainText()
    assert (tmp_path / "modules" / "mod-solocraft" / "mine.cpp").exists()


def test_the_update_press_refuses_a_checkout_of_another_repository(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Round 2's finding 2, at the control: an app claim is not a repository check.

    Mutation: `update-skips-the-origin-check` -- drop the `same_repo()` arm
    from `_update_refusal()` and the press fast-forwards somebody else's
    repository and then deploys this module over it.
    """
    view = _update_view(ps, tmp_path, origin="https://github.com/someone/else.git")

    _press_update(view)

    applier = view.services.applier
    assert isinstance(applier, _FakeApplier)
    assert applier.installed == []
    said = view.module_report.toPlainText()
    assert "someone/else" in said and "mod-solocraft" in said


def test_a_finished_update_drops_the_commits_behind_it_just_pulled(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The count is stale the moment the clone moves, and a stale count is a wrong one.

    Mutation: leave `_behind` alone on a non-remove report and the row goes on
    offering "Update available -- 3 commits behind" over a clone that is now at
    the tip, forever, until the user presses Check for updates again.
    """
    view = _wotlk_modules_view(ps, tmp_path, module=frozenset({"mod-solocraft"}))
    view._behind = {("module", "mod-solocraft"): 3}
    view.reload_modules()
    assert any(
        "Update available" in b.text() for b in view.modules_panel.row("mod-solocraft").chip_buttons
    )

    _deliver_report(view, ApplyReport("install", "mod-solocraft", family="module"))

    assert view._behind == {}
    assert not [
        b
        for b in view.modules_panel.row("mod-solocraft").chip_buttons
        if "Update available" in b.text()
    ]


# --------------------------------------------- the Tuning tab's controls (T44)


def _tuning_view(ps: _Ps, tmp_path: Path) -> ControllerView:
    """A WotLK view with one installed module whose conf is really on disk."""
    conf = tmp_path / "env" / "dist" / "etc" / "modules" / "mod_npc_beastmaster.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("BeastMaster.Enable = 1\n", encoding="utf-8")
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-npc-beastmaster"})}
    )
    return ControllerView(WOTLK, services, status_poll_ms=0)


def test_the_tuning_action_bar_greys_the_two_that_cost_something_until_one_is_owed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T44 item 7. Nothing is waiting when the tab opens, so nothing may be pressed.

    Mutation: enable them at build time and the tab offers to take the server
    down before anybody has changed a setting.
    """
    view = _tuning_view(ps, tmp_path)

    assert view.tuning_restart_button.isEnabled() is False
    assert view.tuning_recreate_button.isEnabled() is False
    assert view.tuning_revert_all_button.isEnabled() is False
    assert view.tuning_reload_button.isEnabled() is True
    assert view.tuning_banner.isHidden() is True


def test_a_save_arms_exactly_the_job_that_file_owes_and_raises_the_banner(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Items 7 and 8. The job comes from `tuning.file_rule()`, never from a guess.

    A conf under `env/dist/etc/` is bound into the containers, so the world
    re-reads it at the next start: a RESTART. The banner names the file that
    is waiting, and its button is the one that answers it.

    Mutation: arm both buttons on every save and the tab offers to replace the
    containers over a change a restart covers.
    """
    view = _tuning_view(ps, tmp_path)
    view._note_tuning_owed("env/dist/etc/modules/mod_npc_beastmaster.conf")

    assert view.tuning_restart_button.isEnabled() is True
    assert view.tuning_recreate_button.isEnabled() is False
    assert view.tuning_banner.isHidden() is False
    assert "mod_npc_beastmaster.conf" in view.tuning_banner_label.text()
    assert view.tuning_banner_button.text() == view.tuning_restart_button.text()


def test_a_file_outside_every_bind_owes_a_recreate_and_the_banner_says_so(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The dearer half of the same rule, and the banner must name the DEARER job.

    Mutation: let the banner's button be whichever job was noted last and a
    user who changed two files presses Restart over a change that needs the
    containers replaced.
    """
    view = _tuning_view(ps, tmp_path)
    view._note_tuning_owed("env/dist/etc/modules/mod_npc_beastmaster.conf")
    view._note_tuning_owed("modules/mod-x/conf/mod-x.conf")

    assert view.tuning_restart_button.isEnabled() is True
    assert view.tuning_recreate_button.isEnabled() is True
    assert view.tuning_banner_button.text() == view.tuning_recreate_button.text()


def test_revert_all_changes_drops_the_unsaved_edits_and_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Item 7's second button, and it is the one that cannot destroy anything.

    It is the undo for "I typed in six boxes and changed my mind": the cards
    are rebuilt from the rows already read, so no file is touched and no disk
    is re-read. The per-card Revert is the one that restores from a backup.

    Mutation: have it call `reload_tuning()` and it silently becomes a second
    Reload from disk -- which also picks up somebody else's edits, which is
    not what a user pressing "revert MY changes" asked for.
    """
    view = _tuning_view(ps, tmp_path)
    card = view.tuning_panel.cards()[0]
    editor = next(iter(card.editors.values()))
    assert editor.control is not None
    before = view.services.controller.server_dir / "env/dist/etc/modules/mod_npc_beastmaster.conf"
    text = before.read_text(encoding="utf-8")
    from PySide6.QtWidgets import QCheckBox, QLineEdit

    if isinstance(editor.control, QLineEdit):
        editor.control.setText("something else")
    elif isinstance(editor.control, QCheckBox):
        editor.control.setChecked(not editor.control.isChecked())
    else:
        editor.control.setValue(editor.control.value() + 1)
    assert view.tuning_revert_all_button.isEnabled() is True

    # Somebody else changes the file while the tab is open. Revert all changes
    # must NOT pick that up -- it is the undo for what this person typed --
    # and Reload from disk must.
    before.write_text("BeastMaster.Enable = 0\n", encoding="utf-8")

    view.tuning_revert_all_button.click()

    assert view.tuning_panel.cards()[0].edits() == {}
    assert view.tuning_revert_all_button.isEnabled() is False
    assert before.read_text(encoding="utf-8") == "BeastMaster.Enable = 0\n", "it wrote to disk"
    reverted = next(iter(view.tuning_panel.cards()[0].editors.values()))
    assert reverted.row.current == "1", "Revert all changes re-read the file"

    view.tuning_reload_button.click()

    reloaded = next(iter(view.tuning_panel.cards()[0].editors.values()))
    assert reloaded.row.current == "0", "Reload from disk did not re-read the file"
    assert text == "BeastMaster.Enable = 1\n"


def test_the_raw_revert_restores_from_the_backup_and_says_which(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Item 15. From the BACKUP, never by re-reading the form.

    Mutation: re-open the file instead of copying the backup over it and
    Revert becomes Reload from disk under another name -- it puts back what
    was just saved, not what was there before.
    """
    view = _tuning_view(ps, tmp_path)
    path = view.services.controller.server_dir / "env/dist/etc/modules/mod_npc_beastmaster.conf"
    view.open_tuning_file("env/dist/etc/modules/mod_npc_beastmaster.conf")

    view.save_tuning_file("BeastMaster.Enable = 0\n")
    assert path.read_text(encoding="utf-8") == "BeastMaster.Enable = 0\n"
    backup = view.tuning_panel.backup_label.text()
    assert ".bak" in backup

    view.revert_tuning_file()

    assert path.read_text(encoding="utf-8") == "BeastMaster.Enable = 1\n"
    assert ".bak" in view.tuning_report.toPlainText()


def test_the_picker_marks_the_core_files_read_only(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Item 13's other half: WHICH files are read-only is the view's list, not the panel's.

    Mutation: pass no `read_only` and `worldserver.conf` sits in the row
    looking exactly like a file this tab will write.
    """
    core = tmp_path / "env" / "dist" / "etc" / "worldserver.conf"
    core.parent.mkdir(parents=True, exist_ok=True)
    core.write_text("[worldserver]\n", encoding="utf-8")
    view = _tuning_view(ps, tmp_path)

    labels = [b.text() for b in view.tuning_panel.file_buttons()]
    assert "worldserver.conf · read-only" in labels


def test_restart_and_recreate_both_ask_first_and_do_nothing_on_no(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both take the server down, so both carry the ellipsis and the dialog.

    The answer is read through `said_yes()`, which is T33's closed bug: the
    static `question()` returns a plain int, so `is StandardButton.Yes` is
    always False -- and here the direction of that bug is a server that never
    restarts, which reads as a dead button.

    Mutation: drop the `_confirm()` call and the button takes the world down
    on the first press, with no warning, from a settings tab.
    """
    from PySide6.QtWidgets import QMessageBox

    view = _tuning_view(ps, tmp_path)
    view._note_tuning_owed("env/dist/etc/modules/mod_npc_beastmaster.conf")
    view._note_tuning_owed("modules/mod-x/conf/mod-x.conf")
    asked: list[str] = []

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: (asked.append(str(args[1])), QMessageBox.StandardButton.No)[1],
    )
    view.tuning_restart_button.click()
    view.tuning_recreate_button.click()

    assert asked == [TUNING_RESTART_LABEL, TUNING_RECREATE_LABEL]
    assert view._busy is False, "a refused dialog must start nothing"
    assert view._tuning_owed, "a refused dialog must forget nothing either"

    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    view.tuning_restart_button.click()
    pump_until(lambda: not view._busy, "the restart never finished")

    assert view._tuning_owed.get("restart") is None, "the restart covered what owed one"
    assert view._tuning_owed.get("recreate"), "and nothing else"


def test_a_finished_recreate_is_reported_on_the_gui_thread_by_a_real_threaded_runner(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T97's second site. `_tuning_job_done` was a closure made per job, and a
    plain callable handed to the job runner is delivered on the WORKER thread,
    so a finished restart or recreate wrote the report, re-armed the bar and
    started the status read from there.

    Every other test in this file runs jobs inline, where the question cannot
    arise, so this one uses the real runner and records the thread each widget
    write lands on. Recreate, because it is the branch that clears BOTH owed
    sets.

    Mutation: hand `_run` a closure again (`lambda answer: self._tuning_job_done(answer)`)
    and the writes are recorded off the GUI thread.
    """
    from PySide6.QtWidgets import QMessageBox

    runners: list[ThreadedJobRunner] = []

    def real_runner(parent: object) -> ThreadedJobRunner:
        runners.append(ThreadedJobRunner(parent))  # type: ignore[arg-type]
        return runners[-1]

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", real_runner)
    view = _tuning_view(ps, tmp_path)
    view._note_tuning_owed("env/dist/etc/modules/mod_npc_beastmaster.conf")
    view._note_tuning_owed("modules/mod-x/conf/mod-x.conf")
    assert view._tuning_owed.get("restart") and view._tuning_owed.get("recreate")

    writes: list[tuple[str, bool]] = []
    for widget, name in (
        (view.tuning_report, "setPlainText"),
        (view.tuning_restart_button, "setEnabled"),
        (view.tuning_recreate_button, "setEnabled"),
    ):
        real = getattr(widget, name)

        def recorded(
            *args: object, _what: str = f"{type(widget).__name__}.{name}", _real: object = real
        ) -> object:
            writes.append((_what, threading.current_thread() is threading.main_thread()))
            return _real(*args)  # type: ignore[operator]

        setattr(widget, name, recorded)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    view.tuning_recreate_button.click()
    pump_until(
        lambda: view.tuning_report.toPlainText() == "recreate: done.", "the recreate's report"
    )
    assert all(runner.wait(HANG_BOUND_MS) for runner in runners)

    assert view._busy is False
    assert view._tuning_owed.get("restart") is None, "a recreate covers the restart it owed"
    assert view._tuning_owed.get("recreate") is None, "and its own"
    off_thread = [what for what, on_gui in writes if not on_gui]
    assert writes and off_thread == [], f"the Tuning bar was written from a worker: {off_thread}"


def test_the_tuning_bar_goes_dead_while_another_action_runs_and_comes_back_to_what_is_owed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The busy gate has to reach this bar: Restart stops the containers an
    install, a rebuild or an importer run is using.

    And it must come back to what is OWED rather than to "on": a job of its own
    finishing must not hand the tab a live "Restart server…" over a change
    nobody made.

    Mutation: re-enable the four unconditionally in `_set_busy(False)` and a
    finished install arms Restart and Recreate on a tab with nothing waiting.
    """
    view = _tuning_view(ps, tmp_path)
    view._note_tuning_owed("env/dist/etc/modules/mod_npc_beastmaster.conf")

    view._set_busy(True)
    assert view.tuning_restart_button.isEnabled() is False
    assert view.tuning_recreate_button.isEnabled() is False
    assert view.tuning_reload_button.isEnabled() is False

    view._set_busy(False)
    assert view.tuning_restart_button.isEnabled() is True, "a restart is still owed"
    assert view.tuning_recreate_button.isEnabled() is False, "nothing owes a recreate"
    assert view.tuning_reload_button.isEnabled() is True


def test_a_failed_install_forgets_the_version_the_clone_may_no_longer_be_at(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Round 2. A failure is not "nothing happened" — the clone may already have moved.

    `Applier.install()` fetches and resets the checkout FIRST and then runs
    deploy, patches, SQL, conf and the client copy. Any of those can raise, and
    by then the folder is at a different commit than the one this tab read. The
    success path drops the cached version through `_note_session_facts()`; the
    failure path left it, so the row went on showing a sha the clone had moved
    off — which is a wrong sha, and worse than no sha at all (T44 item 1's own
    rule).

    Mutation: `failed-install-keeps-the-version` -- drop the `forget()` from
    `_module_failed()` and the row keeps the old sha until something else
    invalidates it.
    """
    read: list[Path] = []
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-solocraft"})}
    )
    object.__setattr__(
        services,
        "module_version",
        lambda path: (read.append(path), f"aaaaaa{len(read)} · 2026-09-01")[1],
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    pump_until(lambda: not view._filling_versions, "the first fill never finished")
    assert view.modules_panel.row("mod-solocraft").version_label.text() == "aaaaaa1 · 2026-09-01"

    view._acting_on = view._manifests[("module", "mod-solocraft")]
    view._module_failed(RuntimeError("the SQL step blew up after the reset"))
    view.reload_modules()
    pump_until(lambda: not view._filling_versions, "the fill after the failure never finished")

    assert view.modules_panel.row("mod-solocraft").version_label.text() == "aaaaaa2 · 2026-09-01"


def test_the_tuning_tab_warns_about_the_keys_this_installs_compose_really_beats(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Round 2's finding 3, end to end: the warning follows the environment, not the file mode.

    The WotLK entry's `world_env` pins `AiPlayerbot.MinRandomBots`, so a conf
    carrying that key is shadowed and a conf carrying only a module's own key
    is not — and until round 2 both got the same warning because both were
    writable.

    Mutation: `tuning-view-passes-no-shadowed` -- drop the `shadowed=` argument
    and the warning is never shown at all, which is the mirror failure: a user
    edits the bot population and nothing tells them the world will ignore it.
    """
    conf = tmp_path / "env" / "dist" / "etc" / "modules" / "mod_npc_beastmaster.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("BeastMaster.Enable = 1\n", encoding="utf-8")
    view = _tuning_view(ps, tmp_path)

    # Same file, same permissions: only the CONTENT decides.
    view.open_tuning_file("env/dist/etc/modules/mod_npc_beastmaster.conf")
    assert not view.tuning_panel.shadow_warning.isVisibleTo(view.tuning_panel)

    conf.write_text("BeastMaster.Enable = 1\nAiPlayerbot.MinRandomBots = 500\n", encoding="utf-8")
    view.open_tuning_file("env/dist/etc/modules/mod_npc_beastmaster.conf")

    assert view.tuning_panel.shadow_warning.isVisibleTo(view.tuning_panel)
    said = view.tuning_panel.shadow_warning.text()
    assert "AiPlayerbot.MinRandomBots" in said
    assert "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS" in said
    assert "BeastMaster.Enable" not in said, "only the shadowed key is named"


def test_the_file_this_install_shadows_most_is_the_one_it_will_not_write(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A fact worth pinning: `playerbots.conf` is where the shadowed keys really live.

    Every `AC_*` row this app writes is an `AiPlayerbot.*` or `Playerbots.*`
    key, and those live in `env/dist/etc/modules/playerbots.conf` -- which is
    in `TUNING_CORE_FILES` and so is shown READ-ONLY (T43's own follow-up). So
    on a shipped install the warning is mostly a statement about a file nobody
    can edit here anyway, and the honest place to change those values is the
    override itself.

    Recorded rather than assumed: if that file ever becomes writable on this
    tab, the warning is what stands between a user and an edit the world
    ignores, and this test is where somebody will find that out.

    Mutation: drop `playerbots.conf` from `TUNING_CORE_FILES` and this fails,
    which is the review this change would owe.
    """
    assert "env/dist/etc/modules/playerbots.conf" in controller_view_module.TUNING_CORE_FILES


# -- T64: "Update the server to latest…" --------------------------------------


_PIN = "993f18094e2f3d38e0f0e6b0a2b4c1d9e8f7a6b5"
"""A catalog `rev`, spelled as the catalog spells one: all forty characters."""


class _LatestSpy:
    """Everything `LatestRoute` is asked, and what it answers.

    A recorder rather than four lambdas, because the interesting assertions are
    about ORDER and COUNT -- did the backup happen before the press, did a
    declined dialog reach the press at all -- and four separate closures cannot
    be asked that.
    """

    def __init__(self) -> None:
        self.presses: list[object] = []
        self.pin_presses: list[object] = []
        self.revs: tuple[native.SourceRev, ...] = ()

    def version(self) -> native.SourceVersion:
        """The REAL rule, over whatever rows this install's record holds.

        Not a line and a flag the test chose: `native.source_version()` is half
        of what T77 fixed, and a spy that answered a boolean would let the view
        test agree with a view that read the record's EXISTENCE -- which is the
        bug (live gate, 2026-09-16, press 6).
        """
        return native.source_version(
            native.InstallState(game_id="wow-wotlk", install_id="x", source_revs=self.revs)
        )

    def route(self) -> native.LatestRoute:
        def press(cancel: object = None) -> Iterator[str]:
            self.presses.append(cancel)
            # What `_record_source_revs()` writes after an update: the checkout
            # is somewhere upstream, the pin is where the gates were.
            self.revs = (native.SourceRev("x/y", "7bcee96 · 2026-09-15", pin=_PIN),)
            yield "--- update-sources"
            yield "moved"

        def to_pin(cancel: object = None) -> Iterator[str]:
            self.pin_presses.append(cancel)
            # And after a return: built == pin, which is what the button's rule
            # has to read. `ahead` stays None because every shipped source but
            # one is a shallow clone and cannot be counted.
            self.revs = (native.SourceRev("x/y", f"{_PIN[:7]} · 2026-09-02", pin=_PIN),)
            yield "back on the pin"

        return native.LatestRoute(
            confirmation=lambda: (
                "Update WoW WotLK in /srv to the newest x/y code?\n\nThis builds code nobody "
                "has tested with this app."
            ),
            press=press,
            pin_confirmation=lambda: (
                "Put WoW WotLK back on the tested commit? It does NOT undo anything the newer "
                "server already wrote into your databases."
            ),
            to_pin=to_pin,
            source_version=self.version,
        )


def _latest(
    ps: _Ps, tmp_path: Path, made: _FakeMaintenance | None = None
) -> tuple[ControllerServices, _LatestSpy]:
    services = _services(ps, tmp_path, [], made)
    spy = _LatestSpy()
    services.update_to_latest = spy.route()
    return services, spy


def _answer(monkeypatch: pytest.MonkeyPatch, which: object) -> list[object]:
    """Answer the REAL three-way dialog with `which`, and keep the box it was asked on.

    `QMessageBox.exec` and not a seam on the view: `ask_update_choice()` is the
    code under test as much as the slot is, and a seam would let these tests
    answer a question whose buttons nothing had checked. The instance is kept
    so the buttons, their labels and the default ARE checked, once, below.
    """
    boxes: list[object] = []

    def exec_(self: object) -> object:
        boxes.append(self)
        return which

    monkeypatch.setattr(controller_view_module.QMessageBox, "exec", exec_)
    return boxes


def test_the_update_button_sits_beside_rebuild_and_is_hidden_where_there_is_no_route(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Hidden rather than greyed, which is this tab's one exception to its own rule.

    A greyed control says "this exists and is not available now". For a server
    adopted from a WSL distro, or an entry whose catalog does not offer the
    route, nothing will ever enable it -- so there is nothing to grey.
    """
    services, _ = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert view.update_to_latest_button.text() == UPDATE_TO_LATEST_BUTTON_LABEL
    assert not view.update_to_latest_button.isHidden()

    bare = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert bare.services.update_to_latest is None
    assert bare.update_to_latest_button.isHidden()
    assert bare.update_to_latest() is False


def test_the_one_dialog_offers_three_buttons_in_the_designs_words_and_defaults_to_cancel(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither the labels nor the default is observable from the outcome.

    So this is the one place the dialog ITSELF is the subject: three buttons,
    the approved design's words on them, and Cancel as both the default (what
    Enter does) and the escape button (what Escape and the title bar's X do).
    An update of untested code is not something Enter should be able to start.
    """
    qmb = controller_view_module.QMessageBox
    boxes = _answer(monkeypatch, qmb.StandardButton.Cancel)
    services, spy = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.update_to_latest() is False
    assert spy.presses == [], "Cancel started the update"
    box = boxes[0]
    # By ROLE and not by position. `QMessageBox` lays its buttons out in the
    # host platform's order, so a positional assertion here would be a claim
    # about the window manager rather than about this dialog, and it would be a
    # different claim on Windows and macOS (both of which this app ships to).
    assert {b.text() for b in box.buttons()} == {
        "Back up first, then update",
        "Update without a backup",
        "Cancel",
    }
    assert box.button(qmb.StandardButton.Yes).text() == "Back up first, then update"
    assert box.button(qmb.StandardButton.Save).text() == "Update without a backup"
    assert (
        box.button(qmb.StandardButton.No) is None
    ), "No is the answer conftest gives an unpatched dialog, and it must mean nothing here"
    assert box.defaultButton() is box.button(qmb.StandardButton.Cancel)
    assert box.escapeButton() is box.button(qmb.StandardButton.Cancel)
    assert "nobody has tested" in box.text()


def test_an_update_answer_the_dialog_does_not_recognise_cancels_rather_than_updating(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The safety property behind the button choice, asserted on the function itself.

    `conftest._no_modal_dialogs` answers every unpatched `exec()` with `No`,
    because `No` is the reply that takes no action. If `No` meant "update
    without a backup" here, every test in this suite that so much as brushed
    this control would start a compile of untested code. `NoButton` is the same
    question: it is what Escape and the window's close button answer.
    """
    qmb = controller_view_module.QMessageBox
    for answer in (
        qmb.StandardButton.No,
        qmb.StandardButton.NoButton,
        int(qmb.StandardButton.Close),
    ):
        _answer(monkeypatch, answer)
        assert ask_update_choice(None, "t", "b") is UpdateChoice.CANCEL, answer


def test_a_real_static_int_answer_is_read_the_same_as_the_enum_member(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T33's shape at the newest site: `==`, never `is`, on either return type.

    PySide6 6.11's dialogs hand back a plain `int` from the static call and a
    `StandardButton` member from an instance's, and `is` against the member was
    always False for the first. Both are driven rather than argued about.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, int(qmb.StandardButton.Save))
    services, spy = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.update_to_latest() is True
    pump_until(lambda: "moved" in view.rebuild_log.text(), "the update reached the panel")
    assert len(spy.presses) == 1, "an int answer did not start the update"


def test_update_without_a_backup_starts_the_press_and_takes_no_backup(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves, because the first alone would be true of a route that backed up anyway.

    Somebody who chose "Update without a backup" chose that; a mysqldump of a
    full world they did not ask for is minutes of their evening and a file on
    their disk.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Save)
    made = _FakeMaintenance()
    services, spy = _latest(ps, tmp_path, made)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.update_to_latest() is True
    pump_until(lambda: "moved" in view.rebuild_log.text(), "the update reached the panel")
    assert len(spy.presses) == 1
    assert spy.presses[0] is not None, "the panel's Stop button has nothing to set"
    assert made.backups == 0, "a backup was taken though the user said not to"


def test_backing_up_first_takes_the_backup_then_asks_again_before_updating(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second question is not a formality: minutes have passed and the answer is on screen.

    No by default, read through `said_yes()`, and the update only starts on an
    explicit Yes. Asserted in ORDER -- the backup happened, and only then was
    the press made -- because "both happened" is also true of a route that
    updated first.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Yes)
    asked: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> object:
        asked.append(text)
        return qmb.StandardButton.Yes

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    made = _FakeMaintenance()
    services, spy = _latest(ps, tmp_path, made)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.update_to_latest() is True
    pump_until(lambda: made.backups == 1, "the backup ran")
    pump_until(lambda: len(spy.presses) == 1, "the update started after the backup")
    assert asked and asked[0].startswith("Backup saved to backups."), asked
    assert "Update now?" in asked[0]


def test_declining_the_second_question_leaves_the_backup_and_starts_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saying no after the backup costs nothing, and the backup is still theirs.

    The report says where it is rather than going quiet: a user who spent five
    minutes on a dump and then changed their mind must not be left wondering
    whether it happened.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Yes)
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: qmb.StandardButton.No,
    )
    made = _FakeMaintenance()
    services, spy = _latest(ps, tmp_path, made)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    view.update_to_latest()
    pump_until(lambda: made.backups == 1, "the backup ran")
    pump_until(
        lambda: "was not started" in view.maintenance_report.toPlainText(),
        "the decline was reported",
    )
    assert spy.presses == []
    assert "backups" in view.maintenance_report.toPlainText()


def test_a_backup_that_fails_stops_the_update_in_dmls_own_words(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dml wow update`'s rule, and not wow-manage's.

    wow-manage offered a backup and continued regardless. Somebody who asked
    for a backup FIRST asked for it because they want one before this runs, and
    "we could not take one, so we did the dangerous thing anyway" is the
    opposite of the answer they gave.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Yes)
    warned: list[str] = []
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "warning",
        lambda parent, title, text, *a, **k: warned.append(text),
    )
    made = _FakeMaintenance()

    def refuse() -> BackupReport:
        raise MaintenanceError("the database container is not running")

    made.back_up = refuse  # type: ignore[method-assign]
    services, spy = _latest(ps, tmp_path, made)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    view.update_to_latest()
    pump_until(lambda: bool(warned), "the failed backup was reported")

    assert spy.presses == [], "the update ran after the backup failed"
    assert warned[0].startswith("Backup failed — the update was not started")
    assert "not running" in warned[0], "the reason was dropped"
    assert failures and failures[0] == warned[0]


def test_a_backup_that_answers_with_something_else_is_treated_as_a_failure(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_backup_done()` ignores a value it cannot draw; here that would be updating blind.

    The two are looking at the same value with different stakes. There, "nothing
    to draw" costs a report line. Here, carrying on would mean updating without
    the backup somebody asked for and with nobody having said it did not happen.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Yes)
    made = _FakeMaintenance()
    made.back_up = lambda: None  # type: ignore[assignment,return-value]
    services, spy = _latest(ps, tmp_path, made)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    view.update_to_latest()
    pump_until(
        lambda: "Backup failed" in view.maintenance_report.toPlainText(),
        "the unusable backup was reported as a failure",
    )
    assert spy.presses == []


def test_the_update_is_refused_while_another_job_is_running_on_this_tab(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebuild's own gates, and this press ENDS in that rebuild.

    Asserted through `_busy` rather than through a running panel, because
    `_busy` is the state an import puts the tab in and an import is the job
    that cannot be stopped at all.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Save)
    services, spy = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view._set_busy(True)

    # FALSE, like every other refusal on this tab: the answer is "was anything
    # started?", and a press that was refused started nothing. It was True here
    # until the cold review of 2026-09-16 pointed out that it made this one
    # press mean something different from `_start_update_to_latest()` and
    # `return_to_the_tested_pin()`, which answer False for the same refusal.
    assert view.update_to_latest() is False
    assert spy.presses == []
    assert view.update_to_latest_button.isEnabled() is False


def test_the_version_line_and_the_way_back_are_drawn_from_one_reading(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """ONE reading decides both, and that is the point of asking once.

    Two readings could disagree -- a press finishing between them is all it
    would take -- and the disagreement's shape is a live "Return to the tested
    pin…" over a line saying the server IS on it.

    They are not the same ANSWER, though, and that is T77: the line is drawn
    whenever there is something to say, the button only while something is off
    its pin. This is the first state, where both are absent.
    """
    services, spy = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert spy.revs == ()
    assert view.source_version_label.text() == ""
    assert view.source_version_label.isHidden()
    assert view.return_to_pin_button.isHidden()

    spy.revs = (native.SourceRev("x/y", "a1b2c3d · 2026-09-16", pin=_PIN, ahead=12),)
    view._refresh_source_version()
    assert view.source_version_label.text() == (
        f"Built from a1b2c3d (2026-09-16), 12 commits past the tested pin {_PIN[:7]}"
    )
    assert not view.source_version_label.isHidden()
    assert not view.return_to_pin_button.isHidden()
    assert view.return_to_pin_button.text() == RETURN_TO_PIN_BUTTON_LABEL


def test_the_way_back_is_hidden_after_a_return_and_offered_again_after_an_update(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T77's finding, driven as the two transitions rather than asserted as a rule.

    The live gate's press 6 (2026-09-16): after a successful return the button
    was still shown and enabled, because `_refresh_source_version()` offered it
    whenever the version line was non-empty and `_record_source_revs()` writes
    `source_revs` after a `to_pin=True` press exactly as it does after an
    update. Pressing it there fetches, moves nothing, and recompiles for the
    better part of an hour to arrive where it already is -- the state the design
    says the control must not be in.

    Both presses go through the real panel and the real dialogs, and the line
    is asserted alongside the button each time: hiding the row entirely after a
    return would also hide the one sentence that says where the server stands.
    """
    qmb = controller_view_module.QMessageBox
    monkeypatch.setattr(
        controller_view_module.QMessageBox, "question", lambda *a, **k: qmb.StandardButton.Yes
    )
    _answer(monkeypatch, qmb.StandardButton.Save)
    services, spy = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.update_to_latest() is True
    pump_until(lambda: not view.return_to_pin_button.isHidden(), "the way back was offered")
    assert view.source_version_label.text() == (
        f"Built from 7bcee96 (2026-09-15); the tested pin is {_PIN[:7]}"
    )

    assert view.return_to_the_tested_pin() is True
    pump_until(lambda: view.return_to_pin_button.isHidden(), "the way back was withdrawn")
    # The LINE stays: the server has been moved and moved back, and that is
    # still a fact about this folder the catalog does not carry.
    assert view.source_version_label.text() == f"On the tested pin {_PIN[:7]} (2026-09-02)"
    assert not view.source_version_label.isHidden()

    # And an update offers it again, which is what says the rule reads the
    # record rather than latching once.
    assert view.update_to_latest() is True
    pump_until(lambda: not view.return_to_pin_button.isHidden(), "the way back came back")


def test_a_mixed_record_still_offers_the_way_back(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """One row home and one not is not "returned", and the press that finishes it must stay.

    WotLK moves two sources. A return whose second source failed, or a record
    written where only one repository had moved in the first place, leaves an
    install whose core is on the pin and whose module checkout is not. `any` and
    not `all` is what keeps the control there; the test exists because `all`
    reads just as plausibly in the source.
    """
    services, spy = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    spy.revs = (
        native.SourceRev("a/b", f"{_PIN[:7]} · 2026-09-02", pin=_PIN),
        native.SourceRev("c/d", "7bcee96 · 2026-09-15", pin=_PIN),
    )
    view._refresh_source_version()

    assert not view.return_to_pin_button.isHidden()
    said = view.source_version_label.text().splitlines()
    assert said[0] == f"a/b: on the tested pin {_PIN[:7]} (2026-09-02)"
    assert said[1] == f"c/d: built from 7bcee96 (2026-09-15); the tested pin is {_PIN[:7]}"


def test_a_read_that_failed_draws_nothing_and_offers_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The fallback is a decision, not a formality, and it goes the careful way.

    `_refresh_source_version()` runs on the reload path and after every job, so
    an exception there takes the tab down over a line of text. What it does with
    the failure is the part worth a test: a read that did not happen knows
    nothing about where the sources stand, and offering an hour of compiling off
    that is worse than offering nothing. Both halves hidden, not just the line.
    """
    services, spy = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    spy.revs = (native.SourceRev("x/y", "7bcee96 · 2026-09-15", pin=_PIN),)
    view._refresh_source_version()
    assert not view.return_to_pin_button.isHidden()

    def unreadable() -> native.SourceVersion:
        raise OSError("the state file could not be read")

    services.update_to_latest = replace(spy.route(), source_version=unreadable)
    view._refresh_source_version()
    assert view.source_version_label.isHidden()
    assert view.source_version_label.text() == ""
    assert view.return_to_pin_button.isHidden()


def test_returning_to_the_pin_asks_yes_no_defaulting_to_no_and_offers_no_backup(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No backup here is deliberate rather than an omission.

    What a backup protects against is a NEW server writing into an old
    database, and that has already happened by the time anybody wants this
    button. The offer belongs to the press that caused it. Offering one here
    would suggest this undoes those writes, which it does not.
    """
    qmb = controller_view_module.QMessageBox
    calls: list[tuple[object, ...]] = []

    def question(*a: object, **k: object) -> object:
        calls.append(a)
        return qmb.StandardButton.Yes

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    made = _FakeMaintenance()
    services, spy = _latest(ps, tmp_path, made)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.return_to_the_tested_pin() is True
    pump_until(lambda: "back on the pin" in view.rebuild_log.text(), "the return reached the panel")
    assert len(spy.pin_presses) == 1
    assert made.backups == 0
    yes = qmb.StandardButton.Yes
    no = qmb.StandardButton.No
    assert calls[0][-2] == yes | no
    assert calls[0][-1] is no, "Enter would start an hour of compiling"
    assert "NOT undo" in str(calls[0][2])


def test_declining_the_return_starts_nothing(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """`conftest` answers No, so this is also what every other test here asserts implicitly."""
    services, spy = _latest(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert view.return_to_the_tested_pin() is False
    assert spy.pin_presses == []
    assert view.rebuild_log.running is False


def test_a_second_update_press_while_the_backup_runs_is_refused(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate neither `_busy` nor the panel can see, and what it cost without one.

    The chained backup runs through `_run()` -- off the GUI thread, for minutes
    -- and `_busy` is the LOG PANEL's flag, set by a job in that panel. A
    `mysqldump` is not one, so a second press during it passed both gates:
    "Update without a backup" then tore the database container down underneath
    the dump that was still running, after which the first press's own handler
    reported "Backup failed -- the update was not started" about a backup the
    user was watching succeed (cold review, 2026-09-16).

    **The second press is made from INSIDE the backup**, which is the only
    moment that window exists and the only way this file can reach it:
    `_inline_jobs` (autouse here) runs every `_run` synchronously, so the seam's
    own body IS the in-flight window. A test that pressed afterwards would be
    testing a state the guard is not about.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Yes)
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: qmb.StandardButton.No,
    )
    told: list[str] = []
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "information",
        lambda parent, title, text, *a, **k: told.append(title),
    )
    during: list[object] = []
    made = _FakeMaintenance()
    real_backup = made.back_up
    view: ControllerView | None = None

    def backup_and_press_again() -> BackupReport:
        assert view is not None
        during.append(view._backup_before_update)
        during.append(view.update_to_latest_button.isEnabled())
        during.append(view.update_to_latest())
        during.append(view.return_to_the_tested_pin())
        return real_backup()

    made.back_up = backup_and_press_again  # type: ignore[method-assign]
    services, spy = _latest(ps, tmp_path, made)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.update_to_latest() is True
    assert made.backups == 1, "the backup never ran, so the window was never entered"
    # In order: the flag was set before the worker started, the buttons were
    # dead, and both presses were refused rather than started.
    assert during == [True, False, False, False], during
    assert spy.presses == [] and spy.pin_presses == []
    assert told and told[0] == "A backup is running", told
    # And it is released: the lock is not a one-way door.
    assert view._backup_before_update is False
    assert view.update_to_latest_button.isEnabled() is True


# -- T76: the backup starts the database when it is down ----------------------


class _FakeDatabase:
    """This install's database container, as the two T76 seams see it.

    A recorder with STATE rather than two counters, because the assertions that
    matter are about the state the backup ran IN and the state the press left
    behind -- "start was called" is also true of a press that started the
    container after the dump, or one that left it running afterwards.
    """

    def __init__(self, *, running: bool, refuses: str = "") -> None:
        self.running = running
        self.refuses = refuses
        self.starts = 0
        self.stops = 0

    def start(self) -> bool:
        """`docker.start_database()`'s contract: True only where it had to start it."""
        self.starts += 1
        if self.refuses:
            raise MaintenanceError(self.refuses)
        if self.running:
            return False
        self.running = True
        return True

    def stop(self) -> None:
        self.stops += 1
        self.running = False

    def alone(self) -> DatabaseAlone:
        return DatabaseAlone(bring_up=self.start, take_down=self.stop)


def _with_database(
    ps: _Ps, tmp_path: Path, db: _FakeDatabase
) -> tuple[ControllerServices, _LatestSpy, _FakeMaintenance]:
    """Services whose backup REFUSES while `db` is down, the way the real one does.

    The fixture is the point. `maintenance.backup()` is a `mysqldump` through
    `docker exec`, and with the container stopped it raises rather than
    returning an empty report. A fake that succeeded either way could not tell
    a press that started the database from one that did not: it would have
    passed before this fix exactly as it passes after it.
    """
    made = _FakeMaintenance()
    real = made.back_up

    def back_up() -> BackupReport:
        if not db.running:
            raise MaintenanceError("ac-database is not running, so there is no database to back up")
        return real()

    made.back_up = back_up  # type: ignore[method-assign]
    services, spy = _latest(ps, tmp_path, made)
    services.database_alone = db.alone()
    return services, spy, made


def test_backing_up_before_an_update_starts_the_stopped_database_and_the_update_proceeds(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T76, through the real dialog: the press a stopped server gets is the one that works.

    Measured on the Vanilla box 2026-09-16 (T64's live gate, `dbdown-*`): with
    every container stopped -- which is how a server nobody is playing on is
    found, and the state somebody is in when they decide to update it -- "Back
    up first, then update" answered *"Backup failed -- the update was not
    started: vanilla-db is not running, so there is no database to back up"* and
    fetched nothing.

    Asserted in the order the fix claims. The backup fake refuses exactly as
    `maintenance.backup()` does, so a route that skipped the start cannot reach
    `spy.presses` at all -- and the last two lines are what say the server was
    left as it was found rather than with a database nobody asked to run.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Yes)
    monkeypatch.setattr(
        controller_view_module.QMessageBox, "question", lambda *a, **k: qmb.StandardButton.Yes
    )
    db = _FakeDatabase(running=False)
    services, spy, made = _with_database(ps, tmp_path, db)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.update_to_latest() is True
    pump_until(lambda: len(spy.presses) == 1, "the update started after the backup")
    assert db.starts == 1, "nothing asked the database to start"
    assert made.backups == 1, "the backup did not run"
    assert db.stops == 1, "the database this press started was left running"
    assert db.running is False, "the server was not left as it was found"


def test_a_database_that_cannot_start_stops_the_update_and_fetches_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure sentence is unchanged, and it now carries docker's own reason.

    `dml wow update`'s rule is untouched by T76: a backup that did not happen
    stops the update. What changed is only what can fail -- the database was
    down and could not be brought up, rather than the database was down.

    The neighbour is asserted too, and it is the half that matters: nothing was
    fetched. A route that reported the failure and pressed anyway would satisfy
    the sentence on its own.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Yes)
    warned: list[str] = []
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "warning",
        lambda parent, title, text, *a, **k: warned.append(text),
    )
    db = _FakeDatabase(
        running=False,
        refuses="ac-database did not report healthy within 180s, so no backup was taken",
    )
    services, spy, made = _with_database(ps, tmp_path, db)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    view.update_to_latest()
    pump_until(lambda: bool(warned), "the failed start was reported")

    assert warned[0].startswith("Backup failed — the update was not started")
    assert "did not report healthy" in warned[0], "docker's own reason was dropped"
    assert spy.presses == [], "the update fetched after the database could not be started"
    assert made.backups == 0
    assert db.stops == 0, "a container that was never started was stopped"


def test_a_database_that_is_already_up_is_not_started_and_not_stopped(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half that would cost somebody their evening if it were wrong.

    Back up now is pressed on servers people are playing on. `start()` answers
    False there -- it started nothing -- and only that answer may run `stop()`.
    A fix that stopped the container unconditionally would take a live server's
    database away underneath its worldserver.
    """
    qmb = controller_view_module.QMessageBox
    _answer(monkeypatch, qmb.StandardButton.Yes)
    monkeypatch.setattr(
        controller_view_module.QMessageBox, "question", lambda *a, **k: qmb.StandardButton.Yes
    )
    db = _FakeDatabase(running=True)
    services, spy, made = _with_database(ps, tmp_path, db)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.update_to_latest() is True
    pump_until(lambda: len(spy.presses) == 1, "the update started after the backup")
    assert made.backups == 1
    assert db.stops == 0, "a database the user had running was stopped by a backup"
    assert db.running is True


def test_the_plain_backup_button_starts_the_database_the_same_way(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """One decision, both places -- T76's own open question, answered yes.

    The plain Backup button failed on a stopped server for exactly the reason
    the update's chained one did, and a user pressing the two an hour apart
    would otherwise get two different answers to "is the database up?". Both go
    through `_backup_with_the_database()`; this is the test that says the second
    button was not forgotten, which is this project's commonest defect shape.
    """
    db = _FakeDatabase(running=False)
    services, _, made = _with_database(ps, tmp_path, db)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    view.back_up()
    pump_until(lambda: made.backups == 1, "the backup ran")
    assert db.starts == 1 and db.stops == 1
    assert db.running is False
    assert "Backed up to backups" in view.maintenance_report.toPlainText()


def test_a_tab_with_no_database_seam_backs_up_exactly_as_it_did_before(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`database_alone=None` is a real state and it must not be a crash.

    It is what a `ControllerServices` built by hand carries -- a test, a future
    game whose wiring is not written yet. The backup still runs; it simply has
    nothing to start, which is what every build before 2026-09-16 did.
    """
    made = _FakeMaintenance()
    services, _ = _latest(ps, tmp_path, made)
    assert services.database_alone is None
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    view.back_up()
    pump_until(lambda: made.backups == 1, "the backup ran without a database seam")


def test_every_game_wires_the_database_seam_to_its_own_container_and_daemon(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam reaches docker, for the right container, on the right daemon.

    "The mechanism exists and nothing calls it" is this project's most expensive
    defect shape, and a `DatabaseAlone` whose halves were never bound to
    `docker.start_database` / `docker.stop_containers` would pass every test
    above -- they all supply their own. So both halves are driven here, through
    the object each game factory really builds, for all four games rather than
    for the one that was being thought about.
    """
    started: list[tuple[str, object]] = []
    stopped: list[tuple[list[str], object]] = []
    reasons: list[str] = []
    monkeypatch.setattr(
        controller_view_module.docker,
        "start_database",
        lambda spec, server_dir, because="", wsl_distro=None: (
            started.append((spec.db, wsl_distro)) or reasons.append(because) or True
        ),
    )
    monkeypatch.setattr(
        controller_view_module.docker,
        "stop_containers",
        lambda names, wsl_distro=None: stopped.append((list(names), wsl_distro)),
    )
    catalog = load_catalog()
    for game in controller_view_module._FACTORIES:
        entry = catalog.get(game)
        services = ControllerServices.for_entry(entry, tmp_path, None, "dml-arch")
        alone = services.database_alone
        assert alone is not None, f"{game} has no database seam"
        assert alone.bring_up() is True
        alone.take_down()
        assert started[-1] == (entry.container_spec().db, "dml-arch"), game
        assert stopped[-1] == ([entry.container_spec().db], "dml-arch"), game
        # `because` completes the timeout sentence `start_database()` raises
        # with, and it must say what was NOT done: a user reading "…did not
        # report healthy within 180s, so no backup was taken" knows the state
        # their server is in, which is the whole job of that sentence.
        assert reasons[-1] == "no backup was taken", game


# --------------------------------------------------------------------------
# T47: the question that stands between a custom install and a `reset --hard`
# over a checkout this app already owns. The dialog is the view's; every
# refusal stays the engine's.
# --------------------------------------------------------------------------

_REPLACE_QUESTION = (
    "modules/mod-my-thing is a checkout of https://github.com/them/mod-my-thing.git, not of "
    "https://github.com/you/mod-my-thing.git.\n\nReplace it?"
)


def _asked(monkeypatch: pytest.MonkeyPatch, answer: object) -> list[tuple[str, str]]:
    """Record every `QMessageBox.question` this view opens and answer them all the same.

    The answer is a plain `int` where a caller passes the enum member, because
    that is what this PySide6's static `question()` really returns (T33) and a
    fake handing back the member hides a comparison made with `is`.
    """
    seen: list[tuple[str, str]] = []

    def question(_parent: object, title: str, text: str, *_a: object, **_k: object) -> object:
        seen.append((title, text))
        return answer

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    return seen


def test_cancelling_the_replace_question_installs_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default answer starts no job at all -- `route` is never called.

    Cancel, Escape and the window's close button all arrive as `NoButton`, and
    `said_yes()` is what makes all three mean no; the `int` below is the No
    button itself, which is the one a mis-spelled comparison would read as yes.
    """
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services)
    route.question = _REPLACE_QUESTION
    seen = _asked(monkeypatch, int(controller_view_module.QMessageBox.StandardButton.No))
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-thing",
    )

    view.install_module_from_link()

    assert [title for title, _text in seen] == ["Replace the checkout of mod-my-thing?"]
    assert seen[0][1] == _REPLACE_QUESTION, "the engine's sentence, not one written here"
    assert route.installed == [], "the install seam was never reached"
    assert view.module_report.toPlainText() == (
        "install from link mod-my-thing: cancelled — nothing on this machine was changed."
    )


def test_answering_the_replace_question_installs_with_the_agreement_carried(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yes reaches the engine as `replacing=True`, which is the only thing it buys.

    The flag is asserted on the seam rather than on the report: an install that
    ran with `replacing=False` after the user said yes would be refused by the
    engine, and the tab would show a refusal for a question they had answered.
    """
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services)
    route.question = _REPLACE_QUESTION
    _asked(monkeypatch, int(controller_view_module.QMessageBox.StandardButton.Yes))
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-thing",
    )

    view.install_module_from_link()

    assert route.installed == [("mod-my-thing", None)]
    assert route.replacing == [True]


def test_an_ordinary_custom_install_opens_no_dialog_and_agrees_to_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing at the clone path means no question -- asserted by making one fatal.

    The commonest press must not grow a dialog, and the way to prove that is
    not to check a message: `QMessageBox.question` itself raises here, so a
    guard that asked anyway fails this test rather than changing its wording.
    """
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services)
    assert route.question is None

    def never(*_a: object, **_k: object) -> NoReturn:
        raise AssertionError("an install with nothing to replace must ask nothing")

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", never)
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-thing",
    )

    view.install_module_from_link()

    assert route.installed == [("mod-my-thing", None)]
    assert route.replacing == [False]


def test_a_seam_that_cannot_answer_asks_nothing_and_leaves_the_refusal_to_the_engine(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A question that cannot be computed must not crash the GUI thread.

    `replacement_question()` reads a claim file and asks git what a checkout is
    a checkout of, and both can fail. The press goes ahead without the question
    -- which is safe in one direction only, and it is this one: the install
    runs with `replacing=False`, so the engine's own guard refuses anything it
    would have asked about and says "Nothing was changed."
    """
    services = _services(ps, tmp_path, [])
    route = _with_custom_route(services)

    def boom(_manifest: object) -> NoReturn:
        raise OSError("git went away")

    services.module_replacement_question = boom
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing to ask")),
    )
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-thing",
    )

    view.install_module_from_link()

    assert route.replacing == [False]


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, by relative path, as bytes. `.git` included."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.mark.skipif(
    not apply_module.git_available(), reason="needs a host git to make a real checkout"
)
def test_cancelling_leaves_a_real_clone_of_another_repository_byte_for_byte(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole route with no fake in the middle: a real checkout, and Cancel.

    `modules/mod-my-thing` here is a real git clone with this app's own claim
    inside it, of a repository that is not the one the pasted link names -- the
    shape a user reaches by installing two forks of the same module. The seams
    are the real `controller_wow_wotlk.modules` bindings over a real `Applier`
    and a real `RunnerGit`, so the question comes from `Applier.
    replacement_question()` and the id from `module_source.derive_link()`.

    What it asserts is the only thing a user cares about: the folder is
    byte-identical afterwards, `.git` included. A `git fetch` + `reset --hard`
    rewrites `.git/FETCH_HEAD` even when it lands on the same commit, so this
    fails for a run that reached the clone seam at all.
    """
    server_dir = tmp_path / "server"
    theirs = tmp_path / "theirs"
    theirs.mkdir(parents=True)
    (theirs / "src").mkdir()
    (theirs / "src" / "Thing.cpp").write_text("// theirs\n", encoding="utf-8")
    author = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=theirs, check=True)
    subprocess.run(["git", "add", "-A"], cwd=theirs, check=True)
    subprocess.run([*["git", *author], "commit", "-qm", "v1"], cwd=theirs, check=True)
    clone = server_dir / "modules" / "mod-my-thing"
    clone.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(theirs), str(clone)], check=True)
    apply_module.write_clone_claim(
        clone, item_id="mod-my-thing", url="https://github.com/them/mod-my-thing.git"
    )
    applier = Applier(
        server_dir,
        git=RunnerGit(),
        remote_url=lambda _dest: "https://github.com/them/mod-my-thing.git",
    )
    services = _services(ps, tmp_path, [])
    services.store = modules.store()
    services.module_from_link = modules.derive_link
    services.module_install_custom = modules.install_custom(applier)
    services.module_replacement_question = modules.replacement_question(applier)
    seen = _asked(monkeypatch, int(controller_view_module.QMessageBox.StandardButton.No))
    view = ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-thing",
    )
    # The `ps` fixture fakes `runner.run` so a `Controller` works without
    # Docker, and a real `RunnerGit` runs through the same seam. Put the real
    # one back now that the view is built: from here on the only subprocess this
    # test starts is git, against a repository on this disk.
    monkeypatch.setattr(runner, "run", _REAL_RUN)
    before = _tree(clone)
    assert before, "the fixture must be a real checkout"

    view.install_module_from_link()

    assert len(seen) == 1, "the real seam found the clone and asked about it"
    assert "https://github.com/them/mod-my-thing.git" in seen[0][1]
    assert "https://github.com/you/mod-my-thing" in seen[0][1]
    assert _tree(clone) == before, "the checkout was not touched"
    assert "cancelled" in view.module_report.toPlainText()


# ------------------------------- T68: a clone whose install never finished keeps Install


class _ClonesFromNothing:
    """A `Git` that fills the destination with `files` instead of cloning.

    The real `Applier` runs here, over a real server directory, so the claim
    these tests read is the one `install()` wrote and not a fixture's idea of
    one. Only the network is faked.
    """

    def __init__(self, files: dict[str, str], origin: str | None = None) -> None:
        self.files = files
        self.clones: list[Path] = []
        # What a checkout at the clone path says it is a checkout OF. `None` is
        # "git would not say", which since T47 (#174) refuses every install
        # over a folder that is already there -- the guard's own subject, and
        # not what these tests are about. A caller that presses Install twice
        # passes the manifest's own URL, which is what a real clone answers.
        self.origin = origin

    def clone(self, spec: object) -> None:
        dest = cast(Path, spec.dest)  # type: ignore[attr-defined]
        self.clones.append(dest)
        for name, text in self.files.items():
            path = dest / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        # `None` while there is no origin to compare against, and "clean" once
        # there is: a caller that presses Install twice is pressing it over the
        # checkout this fake itself just made, and T47's guard asks all three
        # questions before it would reset one. A guard answered `None` refuses,
        # under its own sentence, a press these tests need to reach the applier.
        return None if self.origin is None else True

    def no_local_commits(self, dest: Path, branch: str | None) -> bool | None:
        return None if self.origin is None else True

    def remote_url(self, dest: Path) -> str | None:
        return self.origin


class _RecordingSql:
    def __init__(self) -> None:
        self.files: list[tuple[str, str]] = []
        self.statements: list[tuple[str, str]] = []

    def run_file(self, db: str, path: Path) -> None:
        self.files.append((db, path.name))

    def run_statement(self, db: str, statement: str) -> None:
        self.statements.append((db, statement))


# `mod-arac` is the shipped module this is measured on: family `module`, no
# prompts, nothing in `requires`, and one install-time `direct` world SQL file —
# which is exactly what the T7 guard refuses while the world is up.
ARAC = "mod-arac"
ARAC_SQL = "data/sql/db-world/arac.sql"
ARAC_URL = (
    ManifestStore(modules.BUNDLED_MANIFESTS_DIR, modules.GAME).load("module", ARAC).source.url
)
"""Read off the shipped manifest, never spelled here: T47's repository question
compares this against what the checkout's origin says, and a URL typed into a
fixture would go stale the day the module moves."""


def _arac_view(
    ps: _Ps, tmp_path: Path, *, world_running: bool
) -> tuple[ControllerView, _ClonesFromNothing, _RecordingSql]:
    """The real Modules tab over a real applier and a real server directory.

    Both readings come from `yulon.apply` itself rather than from a mapping this
    test holds: the row is drawn from what the install really left on disk,
    which is the whole question T68 asks.
    """
    # The SQL file the T7 guard refuses, and the two sources the module's other
    # steps copy: `client: Patch-A.MPQ` and `server_dbc: patch-contents/
    # DBFilesContent` both came in with T62 (#171) and raise "client source
    # missing in clone" if the clone this fake makes does not carry them — which
    # would leave every install here UNFINISHED and hide what T68 measures.
    git = _ClonesFromNothing(
        {
            ARAC_SQL: "UPDATE creature_template SET name = 'x';\n",
            "Patch-A.MPQ": "not really an MPQ\n",
            "patch-contents/DBFilesContent/CharBaseInfo.dbc": "not really a DBC\n",
        },
        origin=ARAC_URL,
    )
    sql = _RecordingSql()
    services = _services(ps, tmp_path, [])
    # T62 (#171): `mod-arac` also writes into the game client, so with no client
    # folder set the install now stops at the client notice before it reaches
    # the world-running guard T68 is measured on. The folder is the fixture's
    # own; nothing here asserts anything about what lands in it.
    client_dir = tmp_path / "client"
    (client_dir / "Data").mkdir(parents=True, exist_ok=True)
    object.__setattr__(services, "client_dir", client_dir)
    object.__setattr__(
        services,
        "applier",
        Applier(
            tmp_path,
            git=git,
            sql=sql,
            world_running=lambda: world_running,
            client_dir=client_dir,
        ),
    )
    object.__setattr__(
        services, "installed_modules", lambda: apply_module.installed_clones(tmp_path)
    )
    object.__setattr__(
        services, "unfinished_modules", lambda: apply_module.unfinished_clones(tmp_path)
    )
    return ControllerView(WOTLK, services, status_poll_ms=0), git, sql


def test_an_install_the_running_world_refused_keeps_the_rows_install_button(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T68, measured on the tab: press Install with the world up, and press it again.

    Before this the clone was on disk by the time the guard refused, so the row
    redrew as `Remove` and the refusal's own *"Press Stop, then install again"*
    named a press that was not on the row at all — only in the context menu.

    Three assertions, and the second is the one that keeps the first honest:
    the button says Install, the folder is STILL listed as installed (nothing
    here pretends the clone left), and pressing the button reaches the applier's
    `install()` a second time — read off the clone seam, which is the artefact
    that route leaves behind, not off the report sentence.
    """
    view, git, sql = _arac_view(ps, tmp_path, world_running=True)

    view.modules_panel.row(ARAC).install_button.click()

    assert "the world server is running" in view.module_report.toPlainText()
    assert sql.files == []
    row = view.modules_panel.row(ARAC)
    assert row.data.installed is True and row.data.install_incomplete is True
    assert row.remove_button is None
    assert row.install_button is not None and row.install_button.text() == "Install"
    assert row.install_button.isEnabled()

    row.install_button.click()

    assert git.clones == [tmp_path / "modules" / ARAC] * 2


def test_an_install_that_finished_leaves_the_row_offering_remove(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The control for the test above: same tab, same module, world stopped.

    Only `world_running` differs, so a rule that put Install on every installed
    row — or one that never wrote the finished mark — fails here while the test
    above still passes.
    """
    view, _git, sql = _arac_view(ps, tmp_path, world_running=False)

    view.modules_panel.row(ARAC).install_button.click()

    assert sql.files == [("world", "arac.sql")]
    row = view.modules_panel.row(ARAC)
    assert row.data.installed is True and row.data.install_incomplete is False
    assert row.install_button is None
    assert row.remove_button is not None and row.remove_button.text() == "Remove"


def test_a_clone_from_a_build_before_the_mark_still_offers_remove(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """An older build's clone must not flip to Install the day this ships.

    Every previous build wrote the claim right after the clone and nothing
    else, so its record carries no completion key at all. The fixture is this
    app's own writer with that one key removed — the older record rather than a
    guess at it — and the row it produces is the one the user had yesterday.
    """
    clone = tmp_path / "modules" / ARAC
    clone.mkdir(parents=True)
    apply_module.write_clone_claim(clone, item_id=ARAC, url="https://github.com/x/mod-arac.git")
    claim = clone / apply_module.CLAIM_FILE
    old = json.loads(claim.read_text(encoding="utf-8"))
    del old[apply_module.COMPLETED_KEY]
    claim.write_text(json.dumps(old), encoding="utf-8")

    view, _git, _sql = _arac_view(ps, tmp_path, world_running=True)

    row = view.modules_panel.row(ARAC)
    assert row.data.installed is True and row.data.install_incomplete is False
    assert row.install_button is None
    assert row.remove_button is not None and row.remove_button.text() == "Remove"


def _half_installed(server_dir: Path, folder: str, item_id: str) -> Path:
    """A clone whose claim says this app's install of it never finished.

    Written with the app's own writer rather than hand-rolled JSON, so it is the
    record `install()` leaves behind when a step raises and not a guess at one.
    """
    clone = server_dir / folder / item_id
    clone.mkdir(parents=True)
    apply_module.write_clone_claim(
        clone, item_id=item_id, url=f"https://github.com/x/{item_id}.git", completed=False
    )
    return clone


def test_an_unfinished_clone_whose_requirement_is_gone_offers_a_locked_install(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Round 1's must-fix: T68's Install is still T69's Install, locks and all.

    `mod-city-bots` names `mod-playerbots` in `requires`. A clone of it whose
    install never finished, on a server where that requirement is no longer
    there, now offers Install again — and the applier would refuse that press in
    `_requires_refusal()`. The row therefore has to lock it and say why, which is
    the invariant T55 and T69 established and which the first version of T68
    broke by asking the locks of `not here` rows alone.

    The last assertion is the half that keeps the first honest: the very press
    this row offers is put to the REAL applier, and it refuses. A locked button
    whose applier would have allowed the press would be a different defect.
    """
    _half_installed(tmp_path, "modules", "mod-city-bots")
    view, _git, _sql = _arac_view(ps, tmp_path, world_running=False)

    row = view.modules_panel.row("mod-city-bots")
    assert row.data.installed is True and row.data.install_incomplete is True
    assert row.install_button is not None and row.install_button.text() == "Install"
    assert row.install_button.isEnabled() is False
    assert row.data.installable is False
    assert row.data.install_reason == apply_module.requirement_refusal(
        "mod-city-bots", "mod-playerbots"
    )
    assert [b.text() for b in row.chip_buttons if "mod-playerbots" in b.text()] == [
        modules_panel.chip_needs_label("mod-playerbots")
    ]

    applier = view.services.applier
    assert isinstance(applier, Applier)
    with pytest.raises(apply_module.ApplyError, match="mod-playerbots"):
        applier.install(view._manifests[("module", "mod-city-bots")])


def test_an_unfinished_clone_that_conflicts_with_an_installed_module_locks_its_install(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The other lock, on the other family, and refused by the other guard.

    `buff-mobs` and `nerf-mobs` are declared alternatives. A half-installed
    `buff-mobs` beside a finished `nerf-mobs` offers Install — which
    `_conflict_refusal()` refuses — so the row locks it in the conflict's own
    words.
    """
    clones = "sql_scripts/clones"
    _half_installed(tmp_path, clones, "buff-mobs")
    nerf = tmp_path / clones / "nerf-mobs"
    nerf.mkdir(parents=True)
    apply_module.write_clone_claim(
        nerf, item_id="nerf-mobs", url="https://github.com/x/nerf.git", completed=True
    )
    view, _git, _sql = _arac_view(ps, tmp_path, world_running=False)

    nerf_name = view._manifests[("mod", "nerf-mobs")].name
    row = view.modules_panel.row("buff-mobs")
    assert row.data.install_incomplete is True
    assert row.install_button is not None and row.install_button.isEnabled() is False
    assert row.data.install_reason == modules_panel.conflict_reason(nerf_name)
    assert [b.text() for b in row.chip_buttons if nerf_name in b.text()] == [
        modules_panel.chip_conflicts_with_label(nerf_name)
    ]

    applier = view.services.applier
    assert isinstance(applier, Applier)
    with pytest.raises(apply_module.ApplyError, match="nerf-mobs"):
        applier.install(view._manifests[("mod", "buff-mobs")])


def test_an_unfinished_clone_with_nothing_in_its_way_offers_a_live_install(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The control for the two above: the lock is the exception, not the rule.

    `mod-arac` declares neither a requirement nor a conflict, so its
    half-installed row offers the press with no reason attached — which is what
    T68 is for. A lock arm that locked every unfinished row would pass both
    tests above and fail here.
    """
    _half_installed(tmp_path, "modules", ARAC)
    view, _git, _sql = _arac_view(ps, tmp_path, world_running=False)

    row = view.modules_panel.row(ARAC)
    assert row.data.install_incomplete is True
    assert row.data.installable is True and row.data.install_reason is None
    assert row.install_button is not None and row.install_button.isEnabled() is True


def test_a_broken_custom_manifest_costs_its_own_row_and_the_family_still_draws(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The tab keeps every shipped module and names the one file it could not read (T46).

    Driven through `reload_modules()` against a REAL file on disk rather than a
    `Manifest` handed to the builder: the defect lived in the store, surfaced in
    `_load_manifests()`, and was only ever visible at this call site -- a test
    that injected a broken manifest into the panel would have proved nothing
    about either.

    Before T46 the `!!` line was the ONLY thing this tab drew for the family:
    `list(store.load_all(kind))` is forced inside one `try`, so ~21 shipped
    WotLK modules disappeared behind one file the user's own custom-module route
    had written.
    """
    user = tmp_path / "user-manifests"
    items = user / modules.GAME / "modules"
    items.mkdir(parents=True)
    (user / modules.GAME / "modules.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game": modules.GAME,
                "type": "module",
                # Both bad ones sort FIRST, so a store that stopped at the raise
                # stopped before `mod-kept` -- the user row after the bad one is
                # the half of the family a family-scoped catch never reached.
                "items": ["mod-broken", "mod-bad-shape", "mod-kept"],
            }
        ),
        encoding="utf-8",
    )
    (items / "mod-broken.json").write_text("{not json", encoding="utf-8")
    # Valid JSON that fails the SCHEMA. It is the realistic bad file -- this app
    # persisted these itself, so an older build's shape is what ages badly -- and
    # it does not arrive as `ManifestError`: pydantic raises it, straight through
    # `load_manifest()`, and the skip has to be wide enough to hold it.
    (items / "mod-bad-shape.json").write_text(
        json.dumps(
            {
                "id": "mod-bad-shape",
                "name": 5,
                "type": "module",
                "game": modules.GAME,
                "source": {},
            }
        ),
        encoding="utf-8",
    )
    (items / "mod-kept.json").write_text(
        json.dumps(
            {
                "id": "mod-kept",
                "name": "Kept",
                "type": "module",
                "game": modules.GAME,
                "source": {"repo": "you/mod-kept"},
            }
        ),
        encoding="utf-8",
    )

    services = _services(ps, tmp_path, [])
    services.store = ManifestStore(modules.BUNDLED_MANIFESTS_DIR, modules.GAME, user_root=user)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    # The report box accumulates: constructing the view already reloaded once.
    # Cleared so what is counted below is ONE reload's worth, not the session's.
    view.module_report.clear()

    view.reload_modules()

    # The family is still there. Counted, not sampled: a test that looked for one
    # known id would pass on a tab that drew only that one.
    shipped = services.store.load_index("module").items
    drawn = {r.data.id for r in view.modules_panel.rows()}
    assert set(shipped) <= drawn
    assert len(shipped) >= 20
    # And the USER row that comes after the bad one, which is the half of the
    # loss `module_updates()` used to take silently: the skip has to continue
    # the pass, not merely survive the rows already yielded.
    assert "mod-kept" in drawn
    assert "mod-broken" not in drawn
    assert "mod-bad-shape" not in drawn

    # And the file that would not read is named, once, where every other refusal
    # on this tab is read.
    report = view.module_report.toPlainText()
    # Counted by LINE, not by substring: the id appears twice in its own sentence
    # -- once as the id and once inside the filename -- so `report.count(...)`
    # measures the sentence's shape rather than how many times it was written.
    named = [line for line in report.splitlines() if "mod-broken" in line]
    assert len(named) == 1, report
    assert "is not valid JSON" in named[0]
    # The schema failure gets ONE line too, which is the assertion that fails if
    # the skip is narrowed: a `ValidationError` reaching the report unflattened
    # is five lines, and reaching `_load_manifests()` uncaught is none of them
    # and the family-wide line instead.
    shaped = [line for line in report.splitlines() if "mod-bad-shape" in line]
    assert len(shaped) == 1, report
    assert "name" in shaped[0]
    # Not as the family-wide failure, which is what it used to be.
    assert "could not load modules" not in report

    # And the skips outlive the one thing on this tab that CLEARS the box rather
    # than appending to it. `check_module_updates()` `setPlainText`s its result,
    # which would erase the lines above -- except that `_module_updates_done()`
    # ends in `reload_modules()`, which re-reads the store and appends them
    # again. Asserted rather than reasoned, because the order of those two
    # statements is the whole of it and nothing else pins it.
    view._module_updates_done(())
    after = view.module_report.toPlainText()
    assert "mod-broken" in after and "mod-bad-shape" in after


def test_a_foreign_game_manifest_is_a_reported_skip_and_never_a_row(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Why `Not for this game` is declined, as a test rather than as a comment (T46 item 4).

    T44 item 5 asked for the badge and T46 item 4 carried the ask; the owner
    declined it on 2026-09-15 because every `ManifestStore` path is
    `<root>/<game>/...` and the game is the store's. The only file this tab can
    read already lives in THIS game's directory, so one declaring another game is
    a mis-declared file, not a module that belongs somewhere else -- the badge
    would have labelled the row with something untrue of it.

    What the file gets instead is the skip T46 built: named, with the path and
    what it declared, and no row. The assertion is on the ROW SET, not on the
    absence of a badge string: a test that only checked the badge text would pass
    on a tab that drew the row with any other badge on it.

    The fixture is a real file written to a real user root. T44's round 1 proved
    this row by handing `build_module_rows()` a `Manifest` it built in memory --
    coverage of a row nothing on disk can produce, which is what this replaces.
    """
    user = tmp_path / "user-manifests"
    items = user / modules.GAME / "modules"
    items.mkdir(parents=True)
    (user / modules.GAME / "modules.json").write_text(
        json.dumps(
            {"schema_version": 1, "game": modules.GAME, "type": "module", "items": ["mod-foreign"]}
        ),
        encoding="utf-8",
    )
    # Valid JSON and a valid manifest -- the ONE thing wrong with it is the game,
    # so nothing but the game check can be what refuses it.
    (items / "mod-foreign.json").write_text(
        json.dumps(
            {
                "id": "mod-foreign",
                "name": "Foreign",
                "type": "module",
                "game": "wow-tbc",
                "source": {"repo": "you/mod-foreign"},
            }
        ),
        encoding="utf-8",
    )

    services = _services(ps, tmp_path, [])
    services.store = ManifestStore(modules.BUNDLED_MANIFESTS_DIR, modules.GAME, user_root=user)
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    view.module_report.clear()

    view.reload_modules()

    drawn = {r.data.id for r in view.modules_panel.rows()}
    assert "mod-foreign" not in drawn
    # The family it would have replaced is drawn, which is the T46 half of this.
    assert set(services.store.load_index("module").items) <= drawn

    report = view.module_report.toPlainText()
    named = [line for line in report.splitlines() if "mod-foreign" in line]
    assert len(named) == 1, report
    # The sentence says what it declared and what was expected, so a reader can
    # tell a mis-declared game from an unparseable file without opening either.
    assert "wow-tbc" in named[0] and modules.GAME in named[0]
    assert "could not load modules" not in report


# ---------------------------------------------------------------------------
# T73: who gets the height on the Modules tab.
#
# Measured live on 2026-09-16, maximised at 1920x1080: the module list showed
# about two and a half rows of forty while the report box and the rebuild log
# under it took the rest; reaching City Bots from the top took eleven
# page-downs. T44's approved mockup shows ten rows at once.
#
# EVERY measurement here is made with the theme applied and re-applied at the
# window's width, because that is the only way these numbers mean anything:
# `apply_dadcraft_theme(width=...)` regenerates the stylesheet at a font scale
# derived from the width (`main._Window._restyle_for_width`), and the fonts,
# paddings and `min-height`s it sets are most of what the tab spends. An
# unthemed window says the list has 42% and 5 rows where the themed one says
# 35% and 2 -- the first version of these tests measured the unthemed one and
# was wrong about every number in it.
#
# And through `main.build_catalog_tab()` -- the SAME function `build_window()`
# calls -- with the controller view added to the tab bar the way
# `build_window()` adds it, for the reason `test_catalog_view.py`'s width matrix
# does the same (T28 round 2): the tab bar's frame and the central widget's
# `QVBoxLayout` eat into the budget before a single row is measured.

MODULE_LIST_SHARE_AT_1080P = 0.42
"""How much of the Modules tab's height the list must have, maximised at 1080p.

Measured themed: 311px of the tab's 900 (35%) when T73 was filed, 397 (44%)
now. The tab is exactly full at this size -- every widget is at its own hint or
minimum and there is no surplus for a stretch factor to share -- so the whole
difference is the empty rebuild log no longer asking for 266px to say nothing.
"""

ROWS_VISIBLE_AT_1080P = 8
"""Whole module rows on screen at once, maximised at 1920x1080.

Two before T73, three after it, eight after T75. T73 raised this by giving the
list the height (397px of the tab's 900) and could go no further: a row was
85px, so the list held three whichever way its 397 were shared. T75 is the other
half -- the row is 39px now -- and this number is the two tickets multiplied
rather than either of them alone, which is why both still assert it.
"""

MODULE_LIST_SHARE_WITH_ROOM_TO_SPARE = 0.58
"""And the share on a window big enough to have a surplus (2560x1440).

This is the size at which the stretch factor is the thing being tested: at 1080p
the tab is full and the factor has nothing to share, at 1440p there are ~360
spare pixels and the question is who gets them. The list: 522px/41% and 5 whole
rows before, 757/60% and 7 now. Drop the `1` from `addWidget(self.modules_panel,
1)` and the spare pixels go to the expanding widgets instead.
"""

ROWS_VISIBLE_WITH_ROOM_TO_SPARE = 13
"""Whole rows at 2560x1440: five before T73, seven after it, seventeen after T75.

Thirteen and not seventeen for `ROW_HEIGHT_CEILING`'s reason: the assertion is
the promise (a 1440p screen shows most of the catalog at once), and pinning the
measurement would fail on any honest change to a font. Four rows of headroom is
one card's chrome -- enough that a family growing a header does not turn this
red, and not enough for the row to go back over 60px unnoticed.
"""

TUNING_CARDS_SHARE_AT_1080P = 0.76
"""The Tuning tab's share of its own tab, maximised at 1080p: 73% before, 79%.

Smaller than the Modules tab's gain because there is only one box under the
cards, and it is the box that gained the most from being sized to its text: 164
px of twelve-line hint for a sentence, against the 106 the theme's floor under
any report box gives it.
"""

READABLE_REPORT_LINES = 6
"""The lines a report box must show without scrolling once it holds that many.

Spelled here and NOT read back out of `controller_view.REPORT_LINES`: an
assertion that takes its line count from the constant that sizes the box agrees
with itself whatever that constant says, and three lines would pass it.
"""

A_BOX_THAT_GAVE_NOTHING_BACK = 12
"""What an unsized `QPlainTextEdit` shows -- and so what a report must NOT.

The other half of the same assertion, and the half that says the six is a
CEILING: without it, a box that grows to whatever it holds satisfies "six lines
are readable" by showing twelve, and a report pasted into the tab takes the
rows with it. Twelve because that is the default `sizeHint`, which is what the
boxes here were before.
"""


def _themed_window() -> Any:
    """A window styled the way `build_window()` styles it, before anything is in it."""
    from PySide6.QtWidgets import QMainWindow

    from yulon.ui.theme import apply_dadcraft_theme

    window = QMainWindow()
    apply_dadcraft_theme(window)
    return window


def _controller_in_the_real_window(view: ControllerView, tab_title: str) -> tuple[Any, Any]:
    """Lay `view` out inside the window `build_window()` builds, and show it.

    Returns the window -- so the caller can drive it across the range a user can
    drag it to -- and `view`'s now-current sub-tab.
    """
    import main
    from yulon.ui.catalog_view import CatalogView
    from yulon.ui.widgets.log_panel import LogPanel

    window = _themed_window()
    panel = LogPanel()
    catalog_view = CatalogView(load_catalog(), lambda _entry: None, panel, pick_dir=lambda *_: None)
    tabs, _banner, _splitter = main.build_catalog_tab(window, catalog_view, panel)
    tabs.addTab(view, WOTLK.name)
    tabs.setCurrentWidget(view)
    index = next(i for i in range(view._tabs.count()) if view._tabs.tabText(i) == tab_title)
    view._tabs.setCurrentIndex(index)
    window.setMinimumSize(*main.MINIMUM_WINDOW_SIZE)
    window.show()
    return window, view._tabs.widget(index)


def _at(window: Any, size: tuple[int, int]) -> None:
    """Put the window at `size`, restyle as the app does, and let it settle."""
    from yulon.ui.theme import apply_dadcraft_theme

    window.resize(*size)
    # `main._Window._restyle_for_width`, which is what makes the fonts -- and so
    # every height measured here -- a function of the window's width.
    apply_dadcraft_theme(window, width=window.width())
    process_events()


def _whole_rows_on_screen(panel: modules_panel.ModulesPanel) -> int:
    """How many module rows are completely inside the list's viewport."""
    from PySide6.QtWidgets import QScrollArea

    area = panel.findChild(QScrollArea)
    assert isinstance(area, QScrollArea)
    viewport = area.viewport()
    whole = 0
    for row in panel.rows():
        top = row.mapTo(viewport, row.rect().topLeft()).y()
        if top >= 0 and top + row.height() <= viewport.height():
            whole += 1
    return whole


def _lines_readable_without_scrolling(box: Any, count: int) -> bool:
    """Put `count` lines in `box` and answer whether all of them are on screen.

    Measured against the box's own laid-out viewport rather than recomputed from
    the font: the height under test is itself computed from the font, so a check
    that did the same arithmetic would agree with itself whatever the box did.
    """
    from PySide6.QtGui import QTextCursor

    was = box.toPlainText()
    box.setPlainText("\n".join(f"line {n}" for n in range(1, count + 1)))
    process_events()
    cursor = box.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    bottom_of_the_last_line = box.cursorRect(cursor).bottom()
    nothing_to_scroll = box.verticalScrollBar().maximum() == 0
    box.setPlainText(was)
    return bool(bottom_of_the_last_line <= box.viewport().height() and nothing_to_scroll)


def _a_sentence_wrapping_to(box: Any, lines: int) -> str:
    """ONE paragraph, long enough to wrap to exactly `lines` lines in `box`.

    Built by asking the box's own document how many lines it has made of the
    text so far, because that number depends on the box's width and the theme's
    font and cannot be written down here. One paragraph and not `lines` of them:
    the whole point is a sentence that wraps, which is what every refusal and
    every `TUNING_SAVED` on these tabs is.
    """
    words = ["word"]
    while True:
        box.setPlainText(" ".join(words))
        process_events()
        made = round(box.document().documentLayout().documentSize().height())
        if made >= lines or len(words) > 2000:
            return " ".join(words)
        words.append("word")


def _squeezed(tab: Any) -> list[str]:
    """Everything on `tab` drawn shorter than it says it needs -- i.e. clipped."""
    from PySide6.QtWidgets import QVBoxLayout

    box = tab.layout()
    assert isinstance(box, QVBoxLayout)
    return [
        f"{type(w).__name__}: {w.height()} < {w.minimumSizeHint().height()}"
        for w in (box.itemAt(i).widget() for i in range(box.count()))
        if w is not None and w.isVisible() and w.height() < w.minimumSizeHint().height()
    ]


def test_the_module_list_gets_the_height_on_a_maximised_1080p_window(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The list, not the two boxes under it, is what the tab's height is for.

    The defect this pins: `QVBoxLayout` hands every widget its `sizeHint` before
    it shares anything by stretch factor, so the 3:1:2 this tab was written with
    -- which reads as "the list wins" -- was decided entirely by what the two
    boxes asked for, and an empty `LogPanel` asks for 266px.

    Three assertions, because each on its own is satisfied by the wrong thing: a
    share is met by a list of shorter rows, a row count by a taller window, and
    both by a log that has quietly been made unusable rather than small.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, (1920, 1080))

    share = view.modules_panel.height() / tab.height()
    assert share >= MODULE_LIST_SHARE_AT_1080P, (
        f"the list has {view.modules_panel.height()}px of the tab's {tab.height()} "
        f"({share:.0%}); the report has {view.module_report.height()} and the log "
        f"{view.rebuild_log.height()}"
    )
    whole = _whole_rows_on_screen(view.modules_panel)
    assert (
        whole >= ROWS_VISIBLE_AT_1080P
    ), f"only {whole} of {len(view.modules_panel.rows())} rows are wholly on screen"
    assert view.rebuild_log.height() >= view.rebuild_log.minimumSizeHint().height(), (
        f"the idle log is CLIPPED at {view.rebuild_log.height()}px, not merely small: "
        f"it says it needs {view.rebuild_log.minimumSizeHint().height()}"
    )
    assert _lines_readable_without_scrolling(view.module_report, READABLE_REPORT_LINES), (
        f"the report cannot show {READABLE_REPORT_LINES} lines: "
        f"it is {view.module_report.height()}px"
    )
    assert not _lines_readable_without_scrolling(
        view.module_report, A_BOX_THAT_GAVE_NOTHING_BACK
    ), f"a long report takes the rows with it: the box grew to {view.module_report.height()}px"


def test_a_bigger_screens_spare_height_goes_to_the_module_list(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """At 2560x1440 the tab has pixels to spare, and the list is what spends them.

    The other half of the 1080p test and the half that tests the stretch factor:
    at 1080p this tab is exactly full, every widget on its own hint, and a
    stretch factor decides nothing. Here there are some 360 spare pixels. They
    used to be split with the report and the log -- which have nothing to put in
    them -- and the list came out at 41%.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, (2560, 1440))

    share = view.modules_panel.height() / tab.height()
    assert share >= MODULE_LIST_SHARE_WITH_ROOM_TO_SPARE, (
        f"the list has {view.modules_panel.height()}px of the tab's {tab.height()} "
        f"({share:.0%}); the report has {view.module_report.height()} and the log "
        f"{view.rebuild_log.height()}"
    )
    whole = _whole_rows_on_screen(view.modules_panel)
    assert (
        whole >= ROWS_VISIBLE_WITH_ROOM_TO_SPARE
    ), f"only {whole} of {len(view.modules_panel.rows())} rows are wholly on screen"


def test_the_modules_tab_fits_at_the_size_the_app_opens_at(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """1280x800 is the hard case, not 1920x1080: there is nothing spare at all.

    A height given to the list at the top of the range is taken from somewhere at
    the bottom of it, and the way that is paid for is silent -- Qt draws the
    widgets it cannot fit shorter than their own minimum, and the text inside
    them is simply cut off. Met once already: a report box pinned to six lines
    as a MINIMUM (rather than as a ceiling) clipped itself by 21px here, in the
    window the app opens at, while the 1080p test stayed green.

    Then again at 960x600, the smallest the window can be dragged to, where this
    tab has been over-subscribed since long before T73: the assertion there is
    the narrower one that the report box in particular is not the widget being
    cut, and that the list is never drawn under its OWN minimum.

    That second one said something stronger until T83 -- that the list gets the
    whole of `MODULE_LIST_MIN_HEIGHT` here, which is above its own 70px -- and
    that is no longer true at this one size, on purpose. With the action bar
    wrapped to two lines the tab is four pixels short at 960x600 even with both
    boxes folded away, and the list's floor is the last thing in `_TabFit`'s
    order because a list scrolls: four pixels off it is four pixels of a row
    somebody can still reach, where the same four off the custom-module card is
    a cut word. The floor is still asserted in full at the size the app opens
    at, above, and by
    `test_the_report_box_is_what_gives_after_the_log_and_before_the_list`.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")

    _at(window, main.DEFAULT_WINDOW_SIZE)
    assert (
        _squeezed(tab) == []
    ), f"clipped at the size the app opens at {main.DEFAULT_WINDOW_SIZE}: {_squeezed(tab)}"

    _at(window, main.MINIMUM_WINDOW_SIZE)
    report = view.module_report
    assert report.height() >= report.minimumSizeHint().height(), (
        f"the report is clipped at the smallest window: {report.height()}px against the "
        f"{report.minimumSizeHint().height()} it says it needs"
    )
    # And the list is not CUT here, which is the line between giving height up
    # and being short of it: `_TabFit` may lower the floor to what is left, and
    # may not lower it under what the widget itself says it needs.
    assert view.modules_panel.height() >= view.modules_panel.minimumSizeHint().height(), (
        f"the list is drawn at {view.modules_panel.height()}px against the "
        f"{view.modules_panel.minimumSizeHint().height()} it says it needs at the smallest "
        "window: it was cut rather than asked to give"
    )


def test_a_report_that_wraps_does_not_take_the_list_with_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """What the report box holds is a paragraph, not a tidy list of short lines.

    A refusal on this tab is `str(exc)` -- one long sentence that wraps to
    whatever the box's width makes of it, and the narrower the window the more
    lines that is. The cap has to be counted in LINES ON SCREEN for that, and
    the first version of it was not: it multiplied a whole paragraph's height by
    the number of wrapped lines in it, so one 120-word sentence asked for 967px
    and the list above it was laid out at nothing at all. Every value in the
    `TUNING_SAVED` and `MODULE_SQL_FINISHED` family wraps like this.

    At the size the app OPENS at, because that is where the box is narrow enough
    to wrap and the tab has nothing spare to absorb it.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, main.DEFAULT_WINDOW_SIZE)
    was = view.modules_panel.height()

    view.module_report.setPlainText("word " * 120)
    process_events()

    assert view.module_report.height() <= was, (
        f"one wrapped paragraph took {view.module_report.height()}px, more than the list "
        f"had to start with ({was})"
    )
    assert view.modules_panel.height() >= controller_view_module.MODULE_LIST_MIN_HEIGHT, (
        f"the list is down to {view.modules_panel.height()}px with one sentence in the "
        f"report box, which is {view.module_report.height()}px tall"
    )
    assert _squeezed(tab) == [], f"clipped once the report had something in it: {_squeezed(tab)}"
    assert not _lines_readable_without_scrolling(
        view.module_report, A_BOX_THAT_GAVE_NOTHING_BACK
    ), "the wrapped report is over the ceiling the short-line one is held to"

    # And the other direction, at the size where the tab can afford the box its
    # whole ceiling: a sentence wrapping to exactly the number of lines the box
    # is allowed is a sentence that must be READ, not scrolled. Counting
    # paragraphs instead of the lines they wrap to gets this wrong the quiet
    # way -- the box asks for one line, the theme's floor gives it five, and the
    # sixth is behind a scrollbar nobody looks for.
    _at(window, (1920, 1080))
    view.module_report.setPlainText(
        _a_sentence_wrapping_to(view.module_report, READABLE_REPORT_LINES)
    )
    process_events()
    assert view.module_report.verticalScrollBar().maximum() == 0, (
        f"a sentence wrapping to six lines is scrolled in a box sized for six: "
        f"{view.module_report.height()}px, {view.module_report.verticalScrollBar().maximum()} "
        f"lines of travel"
    )


def test_the_rebuild_log_takes_its_height_back_when_a_job_starts(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The log is small because it is empty, not because it was made small.

    The list's height comes out of a log with nothing in it, which is only honest
    while that stays true: the moment a rebuild or a database update writes to
    the panel, its output is the thing worth the pixels. Driven through
    `LogPanel.run()` -- the call `rebuild_server()` itself makes -- rather than by
    setting the flag, so the cap is lifted by the same signal the app raises.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, (1920, 1080))
    idle = view.rebuild_log.height()
    assert (
        idle == view.rebuild_log.minimumSizeHint().height()
    ), f"an empty log is not at its smallest: {idle}px"

    assert view.rebuild_log.run(lambda: iter(["compiling"]), title="rebuild") is True
    pump_until(lambda: not view.rebuild_log.running, "the job finished")
    process_events()

    assert (
        view.rebuild_log.height() > idle
    ), f"the log is still capped at {view.rebuild_log.height()}px with a job's output in it"


def test_the_tuning_cards_get_the_height_their_report_used_to_take(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The Tuning tab is the same shape, so it gets the same rule.

    One list of cards over one report box, and that box asked for twelve lines of
    a sentence. It is the clearest case of the two: there is no log here, so the
    whole difference between 73% and 79% is the report being as tall as its text.
    """
    view = _tuning_view(ps, tmp_path)
    window, tab = _controller_in_the_real_window(view, "Tuning")
    _at(window, (1920, 1080))

    share = view.tuning_panel.height() / tab.height()
    assert share >= TUNING_CARDS_SHARE_AT_1080P, (
        f"the cards have {view.tuning_panel.height()}px of the tab's {tab.height()} "
        f"({share:.0%}); the report has {view.tuning_report.height()}"
    )
    assert _lines_readable_without_scrolling(view.tuning_report, READABLE_REPORT_LINES), (
        f"the report cannot show {READABLE_REPORT_LINES} lines: "
        f"it is {view.tuning_report.height()}px"
    )
    assert not _lines_readable_without_scrolling(
        view.tuning_report, A_BOX_THAT_GAVE_NOTHING_BACK
    ), f"a long report takes the cards with it: the box grew to {view.tuning_report.height()}px"
    assert "env/dist/etc/modules/playerbots.conf" in controller_view_module.TUNING_CORE_FILES


# ------------------ the press that ran the updater over SQL the app had applied
#
# T78, round-3 live gate press 9c. These go through the REAL binding --
# `controller_wow_wotlk.modules.apply_module_sql()` -- with only Docker faked, so
# what is asserted is the argument the importer would really have been started
# with. A `_FakeImporter` in its place could not see it: the whole defect is in
# the binding these two tests are the only callers of.


ARAC_FILE = "modules/mod-arac/data/sql/db-world/arac.sql"
AOE_FILE = "modules/mod-aoe-loot/data/sql/db-world/aoe_loot_module_string.sql"
APPLYING_AOE = ">> Applying update aoe_loot_module_string.sql"


def _two_modules_on_disk(server_dir: Path) -> None:
    """One shipped module of each route, with its SQL where its manifest says.

    `mod-arac` declares `data/sql/db-world/arac.sql` as `applied_by="direct"`
    and `mod-aoe-loot` declares `data/sql/db-world/*.sql` as `db-import`, in the
    bundled manifests these tests read through the real store.
    """
    for rel in (ARAC_FILE, AOE_FILE):
        path = server_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-- x\n", encoding="utf-8")


def _real_module_sql(
    ps: _Ps,
    tmp_path: Path,
    fake: Callable[..., docker.AttachedRun],
    monkeypatch: pytest.MonkeyPatch,
) -> ControllerView:
    monkeypatch.setattr(docker, "apply_module_sql", fake)
    services = _services(ps, tmp_path, [])
    services.module_sql = lambda output: modules.apply_module_sql(tmp_path, output=output)
    return ControllerView(WOTLK, services, status_poll_ms=0)


def test_the_module_sql_press_never_hands_the_updater_a_file_this_app_applied(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix, asserted on what was SENT rather than on what the panel says about it.

    `AC_UPDATES_ALLOWED_MODULES` is the whole mechanism: a module that is not in
    it is one `UpdateFetcher` never joins a path for, so its `arac.sql` is never
    opened and the exit 1 the gate met cannot happen. The panel's sentences are
    checked too, but the first assertion is the argument itself.
    """
    _two_modules_on_disk(tmp_path)
    handed: list[str | None] = []

    def fake(
        spec: object,
        server_dir: Path,
        *,
        output: Callable[[str], None],
        modules: str | None,
        **kw: object,
    ) -> docker.AttachedRun:
        handed.append(modules)
        output(APPLYING_AOE)
        return docker.AttachedRun(0, (APPLYING_AOE,))

    view = _real_module_sql(ps, tmp_path, fake, monkeypatch)
    view.apply_module_sql()

    assert handed == ["mod-aoe-loot"]
    text = view.module_report.toPlainText()
    assert (
        "sql data/sql/db-world/arac.sql -> world: not handed to the updater: this app "
        "applies it itself at install" in text
    )
    assert "sql data/sql/db-world/aoe_loot_module_string.sql -> world: applied" in text
    assert "FAILED" not in text, text


def test_a_refused_import_still_says_which_file_this_app_owns(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal path reports per file too, and the withheld file is not blamed.

    An importer that exits 1 for a reason of its own must not leave the user
    guessing which of the files on screen it was about -- and the one file this
    app applied itself is the one it was NOT about, because it was never given.
    """
    _two_modules_on_disk(tmp_path)
    words = "ac-db-import exited 1: Could not update the World database"

    def fake(
        spec: object,
        server_dir: Path,
        *,
        output: Callable[[str], None],
        modules: str | None,
        **kw: object,
    ) -> docker.AttachedRun:
        raise docker.DockerCommandError(words)

    view = _real_module_sql(ps, tmp_path, fake, monkeypatch)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.apply_module_sql()

    text = view.module_report.toPlainText()
    assert f"aoe_loot_module_string.sql -> world: refused: {words}" in text
    assert "arac.sql -> world: not handed to the updater: this app applies it itself" in text
    assert "arac.sql -> world: refused" not in text
    assert failures and words in failures[0]


def test_an_install_with_only_direct_sql_asks_for_none_and_still_runs(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`""` has to reach the importer as an empty value, not as an absent one.

    Upstream reads three meanings out of `Updates.AllowedModules`, and the one
    this needs -- `Loading modules: none` -- is the empty string.
    `run_one_shot()` branches on `allowed_modules is None`, so `""` still
    travels as `-e AC_UPDATES_ALLOWED_MODULES=`; a falsy check anywhere on this
    path would turn it into "all", which after a rebuild is the CMake list with
    ARAC compiled in, and the press would apply `arac.sql` again by the other
    door. The run still happens: the core's own updates are the importer's main
    job and this app's direct SQL has nothing to do with them.
    """
    path = tmp_path / ARAC_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("-- x\n", encoding="utf-8")
    handed: list[str | None] = []

    def fake(
        spec: object,
        server_dir: Path,
        *,
        output: Callable[[str], None],
        modules: str | None,
        **kw: object,
    ) -> docker.AttachedRun:
        handed.append(modules)
        return docker.AttachedRun(0, ())

    view = _real_module_sql(ps, tmp_path, fake, monkeypatch)
    view.apply_module_sql()

    assert handed == [""], handed
    assert handed != ["all"]


# ---- T78 round 3: the module the 2026-09-17 live gate found withheld
#
# mod-city-bots is the only shipped manifest where "withhold the file" and
# "withhold the module" differ, and withholding it whole left the world in a
# restart loop on `Table 'acore_world.city_bot_poi' doesn't exist` with the
# app's own remedy -- "Press Apply module SQL" -- unreachable from the button
# that prints it. The assertion below is the argument the importer is started
# with, because that argument IS the mechanism.

CITY_BOTS_SQL: list[dict[str, object]] = [
    {
        "db": "auth",
        "path": "data/sql/db-auth/updates/*.sql",
        "applied_by": "db-import",
    },
    {
        "db": "characters",
        "path": "data/sql/db-characters/updates/*.sql",
        "applied_by": "db-import",
    },
    {"db": "world", "path": "data/sql/db-world/updates/*.sql", "applied_by": "db-import"},
    {
        "db": "playerbots",
        "path": "data/sql/playerbots/updates/2026_07_15_00_citizen_roster.sql",
        "applied_by": "direct",
    },
]
"""mod-city-bots' `sql` block, path for path and route for route.

The manifest itself arrives with T63 and is not on this branch, so the shipped
tree is copied and this one file added to it -- every other manifest the plan
reads here, `mod-arac` included, is the real one, and `ManifestStore`,
`module_sql_plan()`, `module_sql_report()` and the binding are all real too.
"""

CITY_BOTS_FILES = (
    "data/sql/db-auth/updates/2026_07_16_03_stage_cast_one_account_per_bot.sql",
    "data/sql/db-characters/updates/2026_08_22_01_stage_cast_outfits.sql",
    "data/sql/db-world/updates/2026_07_13_01_city_bot_poi.sql",
    "data/sql/playerbots/updates/2026_07_15_00_citizen_roster.sql",
)


def _manifests_with_city_bots(tmp_path: Path) -> Path:
    """The bundled manifest tree, plus mod-city-bots, at a path of our own."""
    root = tmp_path / "manifests"
    shutil.copytree(modules.BUNDLED_MANIFESTS_DIR, root)
    game = root / "wow-wotlk"
    index_file = game / "modules.json"
    index = json.loads(index_file.read_text(encoding="utf-8"))
    index["items"] = sorted([*index["items"], "mod-city-bots"])
    index_file.write_text(json.dumps(index), encoding="utf-8")
    (game / "modules" / "mod-city-bots.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "mod-city-bots",
                "name": "City Bots",
                "type": "module",
                "game": "wow-wotlk",
                "description": "x",
                "source": {"repo": "pjerra/mod-city-bots"},
                "sql": CITY_BOTS_SQL,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_the_module_sql_press_hands_over_city_bots_and_withholds_only_arac(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate's FAIL row 3d, as a press: `mod-city-bots` is in the list again.

    Two modules on disk that differ in one thing only -- where their `direct`
    `.sql` lives. `mod-arac`'s is `data/sql/db-world/arac.sql`, inside a
    directory the updater walks, so it is still withheld and its file still
    reads "not handed to the updater". City Bots' is
    `data/sql/playerbots/updates/…`, which the updater joins no path for, so the
    module goes to the importer and its three db-import groups are applied --
    which is what "Press Apply module SQL, then Start" has to mean for the
    world to come up at all.
    """
    root = _manifests_with_city_bots(tmp_path)
    monkeypatch.setattr(modules, "store", lambda *a, **k: ManifestStore(root, modules.GAME))
    path = tmp_path / ARAC_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("-- x\n", encoding="utf-8")
    for rel in CITY_BOTS_FILES:
        found = tmp_path / "modules" / "mod-city-bots" / rel
        found.parent.mkdir(parents=True, exist_ok=True)
        found.write_text("-- x\n", encoding="utf-8")
    handed: list[str | None] = []
    applying = [
        f">> Applying update {Path(rel).name}" for rel in CITY_BOTS_FILES if "playerbots" not in rel
    ]

    def fake(
        spec: object,
        server_dir: Path,
        *,
        output: Callable[[str], None],
        modules: str | None,
        **kw: object,
    ) -> docker.AttachedRun:
        handed.append(modules)
        for line in applying:
            output(line)
        return docker.AttachedRun(0, tuple(applying))

    view = _real_module_sql(ps, tmp_path, fake, monkeypatch)
    view.apply_module_sql()

    assert handed == ["mod-city-bots"], handed
    text = view.module_report.toPlainText()
    for rel, db in (
        (CITY_BOTS_FILES[0], "auth"),
        (CITY_BOTS_FILES[1], "characters"),
        (CITY_BOTS_FILES[2], "world"),
    ):
        assert f"sql {rel} -> {db}: applied" in text, text
    assert (
        f"sql {CITY_BOTS_FILES[3]} -> playerbots: not handed to the updater: this app "
        f"applies it itself at install" in text
    ), text
    # The sentence the gate photographed, and the module it was wrong about.
    assert "mod-city-bots: not given to ac-db-import" not in text, text
    assert "mod-city-bots was not given to ac-db-import" not in text, text
    # ARAC is untouched by the change, and its reason now names its file.
    assert (
        "mod-arac: not given to ac-db-import -- this app runs data/sql/db-world/arac.sql "
        "itself, and the updater refuses a file it holds no ledger row for." in text
    ), text


# ---------------------------------------------------------------------------
# T75: the row itself, which is what T73 left behind.
#
# T73 gave the list 397px of a 900px tab at 1920x1080 and the list still showed
# three rows, because a row was 85px. Measured themed, the same way and for the
# same reason as everything above: through `main.build_catalog_tab()` with
# `apply_dadcraft_theme(width=...)` re-applied at each width.
#
# Where the 85 went (row `mod-1v1-arena`, 1920x1080, themed):
#
#   | part                              | before | after |
#   | --------------------------------- | -----: | ----: |
#   | outer padding, top + bottom       |     16 |     4 |
#   | name line (name, version, link)   |     22 |    17 |
#   | description (wrapped, 1-2 lines)  |     21 |    17 |
#   | conf paths (one line per file)    |     22 |     0 |
#   | badge line                        |     17 |     0 |
#   | chip line                         |     36 |     0 |
#   | action column (51px button + 18)  |     69 |    35 |
#   | ROW (the tallest column + padding)|     85 |    39 |
#
# The three text lines became two, the badge and the chips moved onto one line
# BESIDE the name instead of two lines under the badge, and the action column
# stopped adding a `QVBoxLayout`'s default 9px margin to a button that was
# already the tallest thing on the row. What is left -- 35px -- is the button,
# and the button is `theme.TOUCH_TARGET_PX` plus the 3px of bevel
# `panel_style.py` records as fixed. The row cannot go under that without
# taking the handheld floor with it, which is why the ceiling below is 60 and
# the row is 39: there is nothing left to spend.

ROW_HEIGHT_CEILING = 60
"""What a module row's minimum height must not exceed at the 1080p font scale.

The owner's number is "about 55" (T75) and the row measures 39. The ceiling is
asserted rather than the measurement, because a test that pinned 39 would fail
on every honest change to a font or a bevel; what must not happen is the row
going back over ~60, which is the height at which eight rows stop fitting.
"""

CHIPS_A_ROW_CAN_CARRY = 8
"""Every chip `_chips_for()` can put on one row, counted from that function.

No install of any catalog produces all eight at once -- `asks a question` and
`needs the client folder` are for a row that is NOT installed, `required by` is
for one that is -- and that is exactly why the widget is tested with all eight
directly. This is a test of the STRIP: the question "what does it do when it is
handed more than fits" has to be asked with more than fits, and the builder's
own worst case (five, on a half-installed row) does not overflow at 1920 wide.
The number is kept in step with `_chips_for()` by
`test_the_chip_strip_is_handed_every_chip_the_builder_can_make`.
"""


def _every_chip() -> tuple[modules_panel.Chip, ...]:
    """One of each chip `_chips_for()` can build, in that function's own order."""
    return (
        modules_panel.Chip(
            "owed", modules_panel.CHIP_REBUILD_PENDING, "not compiled since this changed", "rebuild"
        ),
        modules_panel.Chip("owed", modules_panel.CHIP_SQL_PENDING, "sql on disk, not run", "sql"),
        modules_panel.Chip(
            "owed", modules_panel.chip_update_label(3), "three commits behind", "update"
        ),
        modules_panel.Chip(
            "lock", modules_panel.chip_conflicts_with_label("AH Bot"), "AH Bot is installed here"
        ),
        modules_panel.Chip(
            "lock", modules_panel.chip_needs_label("Playerbots"), "Playerbots is not installed"
        ),
        modules_panel.Chip(
            "fact", modules_panel.CHIP_ASKS_A_QUESTION, "installing opens one dialog first"
        ),
        modules_panel.Chip(
            "fact", modules_panel.CHIP_NEEDS_CLIENT_FOLDER, "no client folder is recorded"
        ),
        modules_panel.Chip(
            "fact", modules_panel.chip_required_by_label(["Solocraft"]), "Solocraft needs this"
        ),
    )


def _a_row_carrying(chips: tuple[modules_panel.Chip, ...], width: int) -> tuple[Any, Any]:
    """A themed `RowWidget` with `chips` on it, laid out `width` pixels wide.

    Themed and SHOWN, because both halves of what is under test are functions of
    the width the widget is really given: a chip's own size hint comes from the
    theme's font and padding, and the strip decides what fits from the width the
    layout hands it. An unparented row answers both questions from a size hint
    nothing has applied.

    The HOST is returned with the row and every caller keeps it in a local, which
    is not tidiness: the row is a child of a top-level widget nothing else holds,
    so dropping the host drops the last Python reference to it, Qt deletes the
    C++ object under the row, and the next line reads
    `Internal C++ object (_ElidedLabel) already deleted` (met on the first run of
    these three tests).
    """
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from yulon.ui.theme import apply_dadcraft_theme

    host = QWidget()
    apply_dadcraft_theme(host, width=1920)
    box = QVBoxLayout(host)
    row = modules_panel.RowWidget(
        modules_panel.ModuleRow(
            id="mod-solocraft",
            family="module",
            name="Solocraft",
            description="Scales dungeons for a small group.",
            url=None,
            installed=True,
            catalogued=True,
            paths=("env/dist/etc/modules/solocraft.conf",),
            chips=chips,
            removable=True,
            remove_reason=None,
            badge=BADGE_INSTALLED,
        ),
        host,
    )
    box.addWidget(row)
    box.addStretch(1)
    host.resize(width, 400)
    host.show()
    process_events()
    return host, row


def test_a_module_row_is_short_enough_that_a_1080p_screen_shows_eight(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The row's own height, which is the half of T44's ten rows T73 could not reach.

    Two assertions and they are different questions. The ROW's minimum is what
    this ticket changed: 85px of layout around a 51px button, measured themed at
    every width from 960 to 2560 and the same 85 at all of them. The COUNT is
    what the owner asked for and it is not implied by the first -- the list is
    397px at this size, and how many rows that holds depends on the card chrome
    above them as well as on the rows.

    The count is also asserted in `test_the_module_list_gets_the_height_...`
    above, through `ROWS_VISIBLE_AT_1080P`, and deliberately so: that test reads
    it as a share of the tab and this one as a property of the row, and the two
    fail for different reasons.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, (1920, 1080))

    tallest = max(view.modules_panel.rows(), key=lambda row: row.minimumSizeHint().height())
    assert tallest.minimumSizeHint().height() <= ROW_HEIGHT_CEILING, (
        f"{tallest.data.id} asks for {tallest.minimumSizeHint().height()}px, over the "
        f"{ROW_HEIGHT_CEILING} a row may take at this font scale"
    )
    whole = _whole_rows_on_screen(view.modules_panel)
    assert whole >= ROWS_VISIBLE_AT_1080P, (
        f"only {whole} of {len(view.modules_panel.rows())} rows are wholly on screen; the "
        f"tallest row asks for {tallest.minimumSizeHint().height()}px"
    )


def test_a_bigger_screen_shows_thirteen_module_rows(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """2560x1440, where the list has 757px and the rows are the only thing spending it.

    Beside the 1080p count rather than instead of it: at 1080p the tab is full
    and the card chrome above the first row is a fifth of what the list has, so
    a change that made rows taller and the chrome shorter could hold the 1080p
    number while losing here, where the chrome is 8% of the list.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, (2560, 1440))

    whole = _whole_rows_on_screen(view.modules_panel)
    assert (
        whole >= ROWS_VISIBLE_WITH_ROOM_TO_SPARE
    ), f"only {whole} of {len(view.modules_panel.rows())} rows are wholly on screen"


def test_no_module_row_is_drawn_shorter_than_it_needs_at_the_smallest_window(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """960x600: the row got shorter, and nothing on it may be CUT to make that true.

    The way a compacted row goes wrong is silent. An elided label reports a
    minimum width of its whole string, so the first version of this row made the
    card wider than the window and the scroll area grew a horizontal bar; the
    fix -- `_ElidedLabel.minimumSizeHint` -- can equally hide a row that is being
    squeezed vertically. So both are asked here: no row is drawn under its own
    minimum, and no row is drawn under the button it carries, which is the
    handheld floor `theme.TOUCH_TARGET_PX` sets and the one size on this row
    that is not this ticket's to spend.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, (960, 600))

    squeezed = [
        f"{row.data.id}: {row.height()} < {row.minimumSizeHint().height()}"
        for row in view.modules_panel.rows()
        if row.height() < row.minimumSizeHint().height()
    ]
    assert squeezed == [], f"rows drawn shorter than they say they need: {squeezed}"
    pressable = [
        row.install_button or row.remove_button
        for row in view.modules_panel.rows()
        if row.install_button is not None or row.remove_button is not None
    ]
    assert pressable, "no row on the shipped WotLK catalog offers a press"
    from yulon.ui.theme import TOUCH_TARGET_PX

    too_small = [b.text() for b in pressable if b.height() < TOUCH_TARGET_PX]
    assert too_small == [], f"a press under the handheld floor at the smallest window: {too_small}"


def test_a_row_carrying_every_chip_shows_them_all_when_the_width_allows(
    qapp: object,
) -> None:
    """Eight chips on one line at a width that fits them, and every one of them drawn.

    The first half of the overflow contract and the half that must not be
    forgotten: a strip that answered "too many, here is the …" at every width
    would satisfy the test below and would have hidden the row's whole state on
    a 4K screen.
    """
    _host, row = _a_row_carrying(_every_chip(), width=3000)

    assert len(row.chip_buttons) == CHIPS_A_ROW_CAN_CARRY
    assert row.chip_strip.visible_chip_labels() == tuple(
        chip.label for chip in _every_chip()
    ), f"only {row.chip_strip.visible_chip_labels()} of the eight are drawn at 3000px"
    assert row.chip_strip.overflow is not None
    assert (
        row.chip_strip.overflow.isVisibleTo(row.chip_strip) is False
    ), "an overflow chip with nothing behind it"


def test_the_chips_a_narrow_row_cannot_fit_are_in_the_overflow_chips_tooltip(
    qapp: object,
) -> None:
    """And at a width that fits some of the eight, the rest are reachable rather than gone.

    1200px because that is a width at which the strip really is short of room
    and really does draw chips -- the assertions below say "some shown, some
    hidden" rather than naming a number, so the test survives a font change; the
    width is what makes both halves non-empty and it is checked as such.

    Four things, because dropping any one of them is a row that lies. Chips must
    still be DRAWN, or "it all fits" and "nothing fits" pass the same test. The
    mark itself must be drawn, or the row simply reads as having three chips.
    What it hides must be all of what is not on screen, label AND sentence,
    because the sentence is the whole of what a fact chip is for. And the chips
    that SURVIVE must be the first ones -- `_chips_for()` puts the jobs somebody
    still has to run before the facts that only explain the row, and an overflow
    that dropped from the front would hide `Rebuild pending` behind a mark while
    showing `conflicts with AH Bot`.

    "The first ones" gained one exception in T83 and exactly one: the LOCK is
    pinned, so it appears in place of the last prefix chip that would otherwise
    have fitted. The fourth assertion is written as "a prefix, plus the lock" for
    that reason -- and both halves are asserted, because a strip that simply
    reordered its chips would satisfy either alone.
    """
    chips = _every_chip()
    _host, row = _a_row_carrying(chips, width=1200)

    strip = row.chip_strip
    shown = strip.visible_chip_labels()
    hidden = strip.hidden_chips()
    assert shown, "the strip drew no chip at all at a width meant to fit some of them"
    assert hidden, f"1200px fitted all eight chips, so nothing overflowed: {shown}"
    assert (
        strip.overflow is not None and strip.overflow.isVisibleTo(strip) is True
    ), f"{len(hidden)} chips are hidden and the '…' that says so is not drawn"
    # A PREFIX, plus the lock -- which is the whole of T83's change to this
    # rule. The strip draws the first chips that fit and the mark stands for the
    # rest, except that the lock is never what the mark stands for: when the
    # width cannot hold both, the lock takes the place of the last prefix chip
    # that would have fitted (`_ChipStrip._plan()`). Dropping the lock out of
    # `shown` here must leave a prefix and nothing else, or the strip is
    # reordering chips rather than pinning one.
    lock = modules_panel.chip_needs_label("Playerbots")
    prefix = tuple(label for label in shown if label != lock)
    everything = tuple(chip.label for chip in chips)
    assert (
        prefix == everything[: len(prefix)]
    ), f"the chips on screen are not the FIRST ones: {shown}"
    assert lock in shown, f"the lock is behind the mark at this width: {shown}"
    tooltip = strip.overflow.toolTip()
    missing = [
        chip.label for chip in hidden if chip.label not in tooltip or chip.detail not in tooltip
    ]
    assert missing == [], f"hidden with nothing to find them by: {missing}"


def test_the_overflow_chip_is_drawn_inside_the_strip_at_every_width(qapp: object) -> None:
    """Dragged across the whole range, the "…" never hangs off the right edge.

    Written because the mutation it kills SURVIVED the two tests above: drop the
    overflow chip's own width from the room `_how_many_fit()` shares out and one
    more real chip fits, which puts the mark past the edge -- where
    `isVisibleTo()` still answers True, the tooltip is still on it, and the user
    simply sees a row that has stopped saying it is hiding anything. No single
    width catches that (at most one extra chip fits, so most widths still have
    room for the mark); a SWEEP does, because somewhere in the range the slack
    after that extra chip is narrower than the mark.

    Every 20px from a strip that fits nothing to one that fits all eight, which
    is also the only assertion here that the strip survives being resized at
    all: `resizeEvent` is what re-places its children and the row is inside a
    list a user drags.
    """
    _host, row = _a_row_carrying(_every_chip(), width=400)
    strip = row.chip_strip
    assert strip.overflow is not None

    hanging = []
    for width in range(400, 3001, 20):
        _host.resize(width, 400)
        process_events()
        if not strip.overflow.isVisibleTo(strip):
            continue
        right = strip.overflow.x() + strip.overflow.width()
        if right > strip.width():
            hanging.append(f"{width}: the '…' ends at {right} in a {strip.width()}px strip")
    assert hanging == [], f"the overflow mark is drawn outside the strip at {hanging[:3]}"


def test_a_locked_row_keeps_the_reason_on_screen_on_a_handheld(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """1280x800 is the Steam Deck, and a Steam Deck cannot hover.

    The defect this pins, measured on the shipped WotLK catalog before the chip
    order was changed: `battlepass` declares a client addon and requires
    `mod-ale`, so at this size it drew `needs the client folder` and put
    `needs AzerothCore Lua Engine (ALE), not installed` behind the overflow mark
    -- the one sentence saying why its Install is dead, in a tooltip a touch
    user has no way to open. `_chips_for()` now ranks the locks ahead of the
    other facts, so the lock is the last fact to go.

    The lock chip is found by its DETAIL matching `row.data.install_reason`,
    which is the sentence the applier would refuse with, rather than by
    re-spelling a label here: the chip and the refusal are meant to be the same
    words, and a test that spelled them itself would pass while they drifted.

    Two things were needed and the second is the one that finished it: the chip
    order alone still left `battlepass` drawing NOTHING at this size, because an
    even split between the row's text column and its status column gave the strip
    373px against a 341px lock chip plus the mark. The status column now takes
    two shares to the text column's one.

    960x600 -- the smallest the window can be DRAGGED to, which is not a device
    -- still puts every long lock chip behind the mark, and this test does not
    claim otherwise. The handheld that ships is 1280x800.

    What stops the first assertion from being vacuous is the test below it, which
    asks the same question of a row carrying all eight chips at a width that
    really is short of room: this one says the shipped catalog is fine on the
    Deck, that one says the RULE is what makes it fine.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, (1280, 800))

    locked = [row for row in view.modules_panel.rows() if row.data.install_reason is not None]
    assert locked, "no row on the shipped WotLK catalog has a locked Install"
    hidden_reasons = []
    for row in locked:
        lock = next(chip for chip in row.data.chips if chip.detail == row.data.install_reason)
        if lock.label not in row.chip_strip.visible_chip_labels():
            hidden_reasons.append(f"{row.data.id}: {lock.label}")
    assert hidden_reasons == [], (
        "the reason Install is locked is behind a tooltip on a touch screen: " f"{hidden_reasons}"
    )

    # The row the review was filed about, by name, because a general assertion
    # over "every locked row" is satisfied by a catalog that happens to have no
    # long lock chips in it today.
    battlepass = view.modules_panel.row("battlepass")
    assert battlepass.data.install_reason is not None, "`battlepass` is no longer a locked row"
    assert modules_panel.chip_needs_label("AzerothCore Lua Engine (ALE)") in (
        battlepass.chip_strip.visible_chip_labels()
    ), (
        "`battlepass` does not say why its Install is dead: "
        f"{battlepass.chip_strip.visible_chip_labels()}"
    )


def test_a_row_too_narrow_for_its_chips_drops_the_facts_before_the_locks(qapp: object) -> None:
    """The RULE behind the test above, asked of a row carrying all eight chips.

    The catalog's own locked rows fit their chips at 1280x800 now, so they cannot
    say what happens when a row does not fit: this one is narrowed until it does
    not. What must survive is owed chips first and the two locks after them; what
    may go is `asks a question`, `needs the client folder` and `required by …`,
    which explain the row rather than explain a dead button.

    Swept rather than fixed at one width, because a single width proves the rule
    only for the one number of chips that happens to fit there.

    Half of the claim and not all of it, deliberately: this hands the strip
    `_every_chip()` and so tests that the strip drops from the END, while the
    test below is what says the BUILDER's end is the plain facts. Reverting the
    order in `_chips_for()` leaves this one green and turns that one and the
    handheld test red -- which is the right split, because the two halves fail
    for different reasons and a strip that dropped from the front would pass
    neither.
    """
    chips = _every_chip()
    _host, row = _a_row_carrying(chips, width=600)
    strip = row.chip_strip
    locks = {
        modules_panel.chip_conflicts_with_label("AH Bot"),
        modules_panel.chip_needs_label("Playerbots"),
    }
    plain_facts = {
        modules_panel.CHIP_ASKS_A_QUESTION,
        modules_panel.CHIP_NEEDS_CLIENT_FOLDER,
        modules_panel.chip_required_by_label(["Solocraft"]),
    }

    squeezed = 0
    wrong = []
    for width in range(600, 3001, 20):
        _host.resize(width, 400)
        process_events()
        hidden = {chip.label for chip in strip.hidden_chips()}
        if not hidden:
            continue
        squeezed += 1
        # A lock may only be hidden once every plain fact already is.
        if hidden & locks and not plain_facts <= hidden:
            wrong.append(f"{width}: hid {sorted(hidden & locks)} while keeping a plain fact")
    assert squeezed, "the row fitted all eight chips at every width in the sweep"
    assert wrong == [], f"the drop order puts a lock before an ordinary fact: {wrong[:3]}"


def test_the_chip_strip_is_handed_every_chip_the_builder_can_make(qapp: object) -> None:
    """`_every_chip()` is every chip `_chips_for()` really returns, and in its order.

    Run through the BUILDER with the shipped WotLK manifests rather than counting
    `chips.append(` in its source, which is what the first version of this did: a
    string count agrees with itself whatever the function returns, and it would
    have stayed green through the reordering this ticket's review asked for --
    the one thing about these chips that is load-bearing.

    Three calls and not one, because the eight contradict each other by
    construction: `asks a question` and `needs the client folder` are only built
    for a row that is NOT installed and `required by` only for one that is, and
    no shipped manifest both asks a question and copies into the client. So the
    reachable cases are asked for separately and unioned, and the ORDER -- which
    is the row's drop order -- is asserted on each of them.

    The manifests come from the shipped store and are picked by the PROPERTY each
    chip needs rather than by id: a test pinned to `mod-ah-bot` says nothing the
    day that module stops asking a question.
    """
    store = modules.store()
    shipped = [m for kind in manifest_store.FAMILY_FILES for m in store.load_all(kind)]
    asks = next(
        m
        for m in shipped
        if any(p.default is None for p in apply_module.required_prompts(m, "install"))
    )
    client_side = next(m for m in shipped if m.client)

    def _built(manifest: Manifest, installed: bool, lock: str) -> tuple[modules_panel.Chip, ...]:
        key = (manifest.type, manifest.id)
        return modules_panel._chips_for(
            manifest,
            manifest.type,
            manifest.id,
            installed,
            modules_panel.SessionState(
                rebuild_owed=frozenset({key}), sql_owed={key: ("one.sql",)}, behind={key: 3}
            ),
            client_dir=None,
            dependants=["Solocraft"],
            blocked_by="AH Bot" if lock == "conflict" else None,
            needs="Playerbots" if lock == "requires" else None,
        )

    asking = _built(asks, False, "conflict")  # owed three, conflict lock, question
    client = _built(client_side, False, "requires")  # owed three, requires lock, client folder
    here = _built(asks, True, "conflict")  # owed three, conflict lock, required by

    expected = [chip.label for chip in _every_chip()]
    made = asking + client + here
    assert {chip.label for chip in made} == set(expected), (
        f"the builder makes {sorted({chip.label for chip in made})}, "
        f"_every_chip() says {sorted(expected)}"
    )
    assert len(expected) == CHIPS_A_ROW_CAN_CARRY
    for built in (asking, client, here):
        labels = [chip.label for chip in built]
        assert labels == [label for label in expected if label in labels], (
            f"the builder's order is {labels}, which is not _every_chip()'s "
            f"({expected}) — and that order is the row's drop order"
        )
    # And the promise the order exists for: after the owed chips, a LOCK comes
    # before any other fact, so the last thing a narrow row hides is the reason
    # Install cannot be pressed (T55/T69, and there is no tooltip on a touch
    # screen). Matched on the DETAIL, which is the applier's own refusal, so the
    # chip and the sentence behind the locked button cannot drift apart.
    locks = {
        modules_panel.conflict_reason("AH Bot"),
        apply_module.requirement_refusal(asks.id, "Playerbots"),
        apply_module.requirement_refusal(client_side.id, "Playerbots"),
    }
    for built in (asking, client, here):
        first_fact = next(chip for chip in built if chip.kind != "owed")
        assert first_fact.kind == "lock" and first_fact.detail in locks, (
            f"the first fact on the row is a {first_fact.kind} reading {first_fact.label}, "
            "which is not the lock"
        )


def test_a_rows_long_description_and_conf_paths_are_a_hover_away(qapp: object) -> None:
    """What the row stopped drawing in full is what its tooltips now carry.

    The description was a WRAPPED label -- two lines at 1280x800 -- and the conf
    files were one line each. Both are one elided line now, which is only honest
    if the whole text is still reachable; an elided label with no tooltip is
    text the app has silently thrown away.
    """
    _host, row = _a_row_carrying((), width=400)

    assert row.description_label.toolTip() == "Scales dungeons for a small group."
    assert row.paths_label is not None
    assert row.paths_label.toolTip() == "env/dist/etc/modules/solocraft.conf"
    assert row.description_label.full_text == "Scales dungeons for a small group."


# ---------------------------------------------------------------------------
# T80: what is left of the tab AFTER a job, and the handles that give it back.
#
# T73 and T75 measured an app that had never run anything. Gate A ran one job on
# a maximised 1080p desktop and the list came back as a row and a half: T73's
# lifted cap is permanent by design, so the log kept ~400px for the rest of the
# session and the hand restarted the app to reach the next row.
#
# Everything below is measured at the DESKTOP shape as well as the bare screen,
# because the two are not the same window and the difference is a row.


DESKTOP_1080P = (1920, 1080 - 47)
"""A window MAXIMISED on a real 1080p desktop, which is not a 1080px window.

47px of the screen belong to the desktop and not to the app: GNOME's top bar and
the window's own title bar, measured on yulon-ubuntu during gate A (round 3,
2026-09-16). Every number T73 and T75 published was taken at the bare 1920x1080,
so each of them was one row optimistic about what a maximised window shows --
T73's "three whole rows" read two live, which is half of why T80 was filed.

Beside the bare screen rather than instead of it: 1920x1080 is also what a
borderless or a non-GNOME desktop really gives, and the pair of them is what says
the 47px no longer costs a row.
"""

ROWS_VISIBLE_AT_DESKTOP_1080P = 8
"""Whole module rows on the maximised desktop with nothing having run yet.

The SAME number as `ROWS_VISIBLE_AT_1080P`, and that is the assertion: before
T80 the desktop's 47px took the bare screen's 8 down to 7, and now both measure
well over it (12 at the desktop shape, 13 at the bare screen) because the two
empty boxes under the list start folded -- an empty report costs 106px of the
theme's own floor and an empty log 180.
"""

ROWS_VISIBLE_AFTER_A_JOB = 5
"""And the count once a rebuild has written to the log, at the desktop shape.

Measured 4 before T80 and 5 after it; 6 at the bare 1920x1080 either way. This
is the number the ticket is about, so it is the one with the least headroom in
this file: one row. The ticket's own first suggestion -- capping the log at a
THIRD of the tab -- measures 4 here, which is why `LOG_SHARE_OF_THE_TAB` is 4
and not 3.
"""


def _the_handle_on(panel: Any) -> Any:
    """The collapse handle on a log panel's strip, found the way a user finds it."""
    from yulon.ui.widgets.log_panel import CollapseHandle

    handle = panel.findChild(CollapseHandle)
    assert handle is not None, "the log panel has no collapse handle on its strip"
    return handle


def _click(widget: Any) -> None:
    """Press `widget` with the real mouse, then let the layout settle."""
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(widget, _Qt.MouseButton.LeftButton)
    process_events()


def _ran_a_job(view: ControllerView, last_line: str = "Compile finished.") -> None:
    """Put a finished rebuild's output in the Modules tab's log.

    Through `LogPanel.run()` -- the call `rebuild_server()` itself makes -- so
    the panel is opened by the same signal the app raises and not by a flag set
    here.
    """
    assert view.rebuild_log.run(lambda: iter(["compiling", last_line]), title="rebuild") is True
    pump_until(lambda: not view.rebuild_log.running, "the job finished")
    process_events()


def _a_rebuild_is_owed(view: ControllerView) -> None:
    """Put the "A rebuild is owed" banner on the Modules tab, the way a press does.

    Through `_deliver_report()` with `rebuild_required=True`, which is the
    `ApplyReport` a real install returns and the only thing that raises this
    banner: `_module_done` notes the fact, `_refresh_rebuild_banner` shows the
    widget. A test that called `setVisible(True)` on `view.rebuild_banner`
    instead would still show a banner and would say nothing about the state a
    user is really in -- and it is the state that matters here, because it is
    also what fills the report box under it.

    It leaves a populated report behind, which is deliberate and is what gate
    round 6 photographed: the install that owes the rebuild is the same press
    whose answer is in the box.
    """
    _deliver_report(
        view, ApplyReport("install", "mod-solocraft", family="module", rebuild_required=True)
    )
    process_events()
    assert view.rebuild_banner.isHidden() is False, "the banner is not on screen, so nothing is set"


def _a_rebuild_is_not_owed(view: ControllerView) -> None:
    """Take the banner back off, through the same session state that raised it."""
    view._rebuild_owed.clear()
    view._refresh_rebuild_banner()
    process_events()
    assert view.rebuild_banner.isHidden() is True, "the banner is still on screen"


def test_the_module_list_keeps_five_rows_after_a_job_at_the_desktop_shape(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The defect gate A found: one rebuild, and the list is a row and a half.

    Four assertions, and each is here because the other three are satisfied by
    something that would not fix this:

    * the ROW COUNT is the complaint, and it is measured at the desktop shape
      rather than the bare screen because that is the window the complaint was
      made about;
    * the log is NOT CLIPPED, so the count cannot be met by squeezing the panel
      under its own minimum -- which is what the tab does on its own when it is
      over-subscribed, and it would read as a fix here while showing a log with
      its strip cut off;
    * the log still HOLDS ITS LAST LINE and is showing it, so the count cannot be
      met by a panel that has been made useless rather than smaller;
    * and the cap is really the thing doing it -- the panel is at its share of
      the tab and not at whatever a job's output happened to ask for.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)

    _ran_a_job(view)
    # And a report in the box under it, because a press that starts a rebuild
    # writes one: the two boxes take the tab together or not at all.
    view.module_report.setPlainText("Rebuild finished.")
    process_events()

    whole = _whole_rows_on_screen(view.modules_panel)
    assert whole >= ROWS_VISIBLE_AFTER_A_JOB, (
        f"after one job only {whole} of {len(view.modules_panel.rows())} rows are wholly on "
        f"screen at {DESKTOP_1080P}: the list has {view.modules_panel.height()}px of the "
        f"tab's {tab.height()}, the log {view.rebuild_log.height()} and the report "
        f"{view.module_report.height()}"
    )
    assert view.rebuild_log.height() >= view.rebuild_log.minimumSizeHint().height(), (
        f"the log is CLIPPED at {view.rebuild_log.height()}px, not merely capped: it says "
        f"it needs {view.rebuild_log.minimumSizeHint().height()}"
    )
    assert not view.rebuild_log.collapsed, "a finished job left its own output folded away"
    assert (
        "Compile finished." in view.rebuild_log.text()
    ), f"the log does not hold the job's last line: {view.rebuild_log.text()!r}"
    share = tab.height() // controller_view_module.LOG_SHARE_OF_THE_TAB
    assert view.rebuild_log.maximumHeight() == share, (
        f"the log is not held to its share of the tab: its cap is "
        f"{view.rebuild_log.maximumHeight()}px against the {share} a quarter of "
        f"{tab.height()} comes to"
    )


def test_the_desktop_shapes_missing_47px_no_longer_costs_a_module_row(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Maximised on a real desktop shows what the bare screen does, idle.

    T73 and T75 both measured 1920x1080 exactly, and a maximised GNOME window is
    47px shorter than that, so both published a count a live hand could not
    reproduce. Asserted against the same constant as the bare screen on purpose:
    the claim is that the two shapes agree, and a separate smaller number here
    would let them drift apart again silently.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)

    whole = _whole_rows_on_screen(view.modules_panel)
    assert whole >= ROWS_VISIBLE_AT_DESKTOP_1080P, (
        f"only {whole} of {len(view.modules_panel.rows())} rows are wholly on screen "
        f"maximised at {DESKTOP_1080P}: the list has {view.modules_panel.height()}px of "
        f"the tab's {tab.height()}"
    )
    assert _squeezed(tab) == [], f"clipped on the maximised desktop: {_squeezed(tab)}"


def test_the_log_strip_folds_the_output_away_and_the_list_takes_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A click on the handle, and the height the log kept goes to the list.

    Driven with `QTest.mouseClick` on the handle the panel really builds -- found
    through `findChild`, which is as much as a user has -- rather than by calling
    `set_collapsed()`: a toggle nothing on screen can reach is not a toggle.

    Both directions, because a fold that cannot be undone is a hidden panel:
    the second click must bring the output back, with the job's line still in it.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    open_log = view.rebuild_log.height()
    open_list = view.modules_panel.height()
    handle = _the_handle_on(view.rebuild_log)

    _click(handle)

    assert view.rebuild_log.collapsed, "the click did not fold the log"
    assert (
        view.rebuild_log.height() < open_log
    ), f"the log is folded but still {view.rebuild_log.height()}px, against {open_log} open"
    assert view.modules_panel.height() > open_list, (
        f"the log folded and the list did not grow: {view.modules_panel.height()}px, "
        f"against {open_list} with the log open"
    )
    assert view.rebuild_log.status_text() != "", "folding the log took its strip with it"

    _click(handle)

    assert not view.rebuild_log.collapsed, "the second click did not bring the log back"
    assert (
        view.rebuild_log.height() == open_log
    ), f"the log came back at {view.rebuild_log.height()}px, not the {open_log} it had"
    assert "Compile finished." in view.rebuild_log.text(), "folding the log lost its output"


def test_a_folded_log_unfolds_itself_when_the_next_job_starts(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Whatever the user folded away, the next job's output is worth showing.

    The one thing the fold must not do is make a rebuild silent. Two jobs, the
    fold between them, and the second one drives the panel through `run()` -- so
    what unfolds it is `run_started`, the signal the app raises, and not a call
    this test makes.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view, last_line="the first job")
    _click(_the_handle_on(view.rebuild_log))
    assert view.rebuild_log.collapsed, "the fold this test is about did not happen"

    _ran_a_job(view, last_line="the second job")

    assert not view.rebuild_log.collapsed, "a new job ran with its output still folded away"
    assert "the second job" in view.rebuild_log.text()


def test_an_empty_report_box_is_folded_and_a_report_unfolds_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The report box is 106px of nothing until there is a report, and then it is not.

    Two halves of one rule and they fail for different reasons: the box is folded
    while empty (which is every start, and the moment the list most needs the
    height), and it is open the moment anything is written to it (a press whose
    answer is hidden is a press that looks like it did nothing).

    The unfold is driven by `setPlainText` -- what `_format_report` and every
    refusal on this tab do -- and not by the strip, because the strip is not what
    the app calls.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)

    assert view.module_report_strip.collapsed, "an empty report box is open on a fresh tab"
    assert not view.module_report.isVisible(), "the folded report box is still drawn"
    folded_list = view.modules_panel.height()

    view.module_report.setPlainText("solocraft: installed.")
    process_events()

    assert not view.module_report_strip.collapsed, "a report arrived and the box stayed folded"
    assert view.module_report.isVisible()
    assert (
        view.modules_panel.height() < folded_list
    ), "the report box unfolded and took nothing from the list, so it is not on screen"

    _click(view.module_report_strip)

    assert view.module_report_strip.collapsed, "the click did not fold the report away"
    assert view.modules_panel.height() == folded_list, (
        f"the folded report gave the list {view.modules_panel.height()}px, not the "
        f"{folded_list} it had before the report arrived"
    )
    assert (
        view.module_report.toPlainText() == "solocraft: installed."
    ), "folding the report box threw its text away"


def test_the_tuning_report_folds_the_same_way_and_the_cards_take_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The Tuning tab is the same shape, so it gets the same handle.

    Its own test and not a parametrisation of the Modules one: the two tabs build
    their strips separately, and the way this goes wrong is one of them being
    wired and the other not.
    """
    view = _tuning_view(ps, tmp_path)
    window, _tab = _controller_in_the_real_window(view, "Tuning")
    _at(window, DESKTOP_1080P)

    assert view.tuning_report_strip.collapsed, "an empty tuning report is open on a fresh tab"
    folded_cards = view.tuning_panel.height()

    view.tuning_report.setPlainText("solocraft: wrote 2 keys.")
    process_events()
    assert not view.tuning_report_strip.collapsed, "a report arrived and the box stayed folded"
    open_cards = view.tuning_panel.height()
    assert open_cards < folded_cards

    _click(view.tuning_report_strip)

    assert view.tuning_report_strip.collapsed
    assert view.tuning_panel.height() > open_cards, (
        f"the tuning report folded and the cards did not grow: {view.tuning_panel.height()}px, "
        f"against {open_cards} with it open"
    )


# ---------------------------------------------------------------------------
# T83: the two things the Modules tab does below the handheld width, measured
# at `main.MINIMUM_WINDOW_SIZE` -- which is a width a user can really drag to
# and which nothing before round 4's gate had ever been measured at.
#
# Both defects are the same shape and it is the silent one: Qt's answer to "this
# does not fit" is to draw it smaller than it says it needs and cut the text
# off. There is no ellipsis, no tooltip and no warning. Measured themed at 960
# before the fix: the toolbar's `Check for updates` was drawn in 114px against
# the 144 it asked for and read `heck for update`, and `accountwide`'s chip
# strip was 281px against a 316px lock chip, so the row that says why its
# Install is dead drew nothing at all.


TOOLBAR_WIDTHS = ((960, 600), (1000, 700), (1280, 800))
"""The three widths the Modules toolbar is measured at, narrowest first.

960x600 is `main.MINIMUM_WINDOW_SIZE` and the hard case. 1000x700 is the window
gate A2 was taken in, and it is here because it is the one a human reported --
a test that only covered the extreme would not have failed on the screenshot
the ticket was filed from. 1280x800 is the shape the app opens at, where the bar
has always fitted on one line and must go on doing so.
"""


def _module_toolbar_buttons(view: ControllerView) -> list[Any]:
    """Every button on the Modules action bar, read off the BAR and not listed here.

    Off `view.module_actions`' own layout, because a list written out here would
    be a second opinion about what is on that bar: a seventh button added to it
    is exactly where this ticket's defect would come back, and a hand-written
    list would go on passing about the six it knew.

    **The ones the bar is SHOWING**, which is not a narrowing of that rule but
    the whole of what it is about. T64 put two more buttons on this bar --
    "Update the server to latest…" and "Return to the tested pin…" -- and both
    are hidden until the entry has a route and the install has something to
    return from. `FlowLayout` does not lay a hidden item out at all, so those
    two keep the 100x30 every Qt widget starts at, and `_clipped()` read that
    default as a label cut in half on a button nobody can see. The moment
    either is shown it is laid out, comes back here, and is measured like the
    rest; a button added to this bar and left visible is still caught.
    """
    from PySide6.QtWidgets import QPushButton

    bar = view.module_actions.layout()
    found = [bar.itemAt(i).widget() for i in range(bar.count())]
    buttons = [w for w in found if isinstance(w, QPushButton)]
    assert len(buttons) == len(found), f"something on the action bar is not a button: {found}"
    return [b for b in buttons if b.isVisibleTo(view.module_actions)]


def _clipped(button: Any) -> str | None:
    """Why `button`'s label does not fit its width, or `None` when it does.

    The button's own font metrics against its own geometry, with the chrome
    taken from the difference between its size hint and the width of its text:
    the padding is the theme's, the border is the theme's, and a number typed in
    here would be a third opinion that agrees with itself whatever the theme
    does.
    """
    text = button.text()
    advance = button.fontMetrics().horizontalAdvance(text)
    chrome = max(0, button.sizeHint().width() - advance)
    if button.width() >= advance + chrome:
        return None
    return f"{text!r}: {button.width()}px for {advance}px of text plus {chrome}px of chrome"


def _bar_holds_every_line(view: ControllerView) -> list[str]:
    """The buttons drawn outside the action bar they are on, which must be none."""
    bar = view.module_actions
    return [
        f"{b.text()!r} at ({b.x()},{b.y()}) {b.width()}x{b.height()} "
        f"in a {bar.width()}x{bar.height()} bar"
        for b in _module_toolbar_buttons(view)
        if b.y() < 0 or b.y() + b.height() > bar.height() or b.x() + b.width() > bar.width()
    ]


def test_every_modules_toolbar_button_reads_whole_at_every_width(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The defect gate A2 photographed: `Check for updates` drawn as `heck for update`.

    Three assertions at each width, and the second and third are here because a
    "fix" that met only the first is worse than the bug:

    * every label FITS, which is the complaint;
    * every button is inside the bar it is on, so the labels cannot be made to
      fit by letting the bar overflow the tab -- which is what a `QHBoxLayout`
      given a minimum width would do, moving the clipping one widget out, and it
      is also what a wrapped bar does the moment `flow_bar()`'s size policy stops
      declaring `heightForWidth` (the second line is then painted over the module
      list, and every other assertion here stays green);
    * and at 1280 the bar is still ONE line, which is what stops the whole thing
      being "solved" by wrapping at every window shape and spending a row of the
      list to do it.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")

    for size in TOOLBAR_WIDTHS:
        _at(window, size)
        buttons = _module_toolbar_buttons(view)
        assert len(buttons) >= 6, f"only {len(buttons)} buttons on the Modules action bar"
        clipped = [why for why in (_clipped(b) for b in buttons) if why is not None]
        assert clipped == [], f"toolbar text cut off at {size}: {clipped}"
        outside = _bar_holds_every_line(view)
        assert outside == [], f"a button is drawn outside the action bar at {size}: {outside}"

    _at(window, (1280, 800))
    tops = sorted({b.y() for b in _module_toolbar_buttons(view)})
    assert len(tops) == 1, f"the bar wrapped at the size the app opens at: {tops}"


def test_the_modules_toolbar_wraps_rather_than_shrinking_at_the_smallest_window(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """And the mechanism, because "nothing is clipped" has other solutions.

    A bar whose buttons were given shorter labels would pass the test above, and
    so would one that dropped four of them into a `More…` menu. What is asserted
    here is that the same six buttons are all still DRAWN, on more than one line,
    and each at its own full size hint -- which is the difference between
    wrapping and the `QHBoxLayout` this replaced, whose only answer to a bar that
    does not fit is to shrink every item in it.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, main.MINIMUM_WINDOW_SIZE)

    buttons = _module_toolbar_buttons(view)
    tops = sorted({b.y() for b in buttons})
    assert len(tops) > 1, (
        f"the bar is still one line at {main.MINIMUM_WINDOW_SIZE}, so it did not wrap: "
        f"{[(b.text(), b.width(), b.sizeHint().width()) for b in buttons]}"
    )
    shrunk = [
        f"{b.text()!r}: {b.width()} < {b.sizeHint().width()}"
        for b in buttons
        if b.width() < b.sizeHint().width()
    ]
    assert shrunk == [], f"a wrapped bar still squeezed its buttons: {shrunk}"
    assert all(b.isVisible() for b in buttons), "wrapping hid a button instead of moving it"
    assert _bar_holds_every_line(view) == [], "the bar is shorter than the lines it drew"


def _a_flow_bar_in_a_tight_column(labels: list[str], width: int, spare: int) -> tuple[Any, Any]:
    """A `FlowBar` of buttons in a column that has `spare` pixels to give away.

    The bar is tested through a `QVBoxLayout` and not on its own, because every
    claim it makes is a claim about what a PARENT layout does with it: the size
    policy decides whether it is asked for `heightForWidth()` at all, and the
    minimum decides what happens when there is not enough height to go round.

    The second widget is what makes the column tight. It has a minimum height
    and it expands, so the column's demand is its minimum plus the bar's, and
    `spare` says how many pixels over that the host is made -- zero is a column
    with nothing to give away, which is the state in which the bar's claim on
    the height has to be honoured rather than merely preferred.

    Returns the host (which every caller must keep in a local: dropping it
    deletes the C++ objects under the bar) and the bar.
    """
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

    from yulon.ui.theme import apply_dadcraft_theme
    from yulon.ui.widgets.flow_layout import flow_bar

    host = QWidget()
    apply_dadcraft_theme(host, width=1280)
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    bar = flow_bar(host)
    for label in labels:
        bar.flow().addWidget(QPushButton(label, bar))
    column.addWidget(bar)
    filler = QWidget(host)
    filler.setMinimumHeight(200)
    column.addWidget(filler, 1)
    host.resize(width, 400)
    host.show()
    process_events()
    host.resize(width, bar.flow().heightForWidth(width) + 200 + spare)
    process_events()
    return host, bar


THE_SIX_ON_THE_MODULES_BAR = [
    "Check for updates",
    "Refresh",
    "Adopt as imported…",
    "Apply pending database updates…",
    "Apply module SQL",
    "Rebuild the server…",
]
"""The Modules action bar's own labels, for the widget tests below.

Copied deliberately rather than imported: these are tests of `FlowBar`, and what
they need is a realistic set of button widths, not today's Modules tab. The tab
is asserted separately and through the real window.
"""


def test_a_column_gives_a_flow_bar_the_height_its_lines_need(qapp: object) -> None:
    """The claim a wrapping bar lives or dies by, asked of the COLUMN it is in.

    Everything else about wrapping can be right and still produce a bar drawn
    one line tall with its second line painted over the widget beneath it: the
    buttons are at their hints, visible and unclipped horizontally, and every
    assertion that looks at the bar alone stays green. What decides it is
    whether the parent layout asks for `heightForWidth()` and honours the
    answer, and that is a question about `FlowLayout.hasHeightForWidth()` and
    `_lay()` agreeing with `setGeometry()` -- which is why they are one function
    (see `FlowLayout._lay`).

    Asked with a column that has nothing spare, so the answer cannot come from
    surplus height the bar was handed for another reason.
    """
    host, bar = _a_flow_bar_in_a_tight_column(THE_SIX_ON_THE_MODULES_BAR, width=700, spare=0)
    flow = bar.flow()
    needed = flow.heightForWidth(bar.width())

    assert len(flow._lines(bar.width())) > 1, "700px did not wrap the bar, so nothing is tested"
    assert bar.height() >= needed, (
        f"the column gave the bar {bar.height()}px for the {needed} its "
        f"{len(flow._lines(bar.width()))} lines take"
    )
    buttons = [flow.itemAt(i).widget() for i in range(flow.count())]
    assert max(b.y() + b.height() for b in buttons) <= bar.height(), (
        "a button is drawn below the bottom of the bar: "
        f"{[(b.text(), b.y(), b.height()) for b in buttons]}"
    )
    host.hide()


def test_a_flow_bar_re_states_its_height_when_a_drag_changes_the_line_count(
    qapp: object,
) -> None:
    """Dragged from a width that fits one line to one that does not, and back.

    What makes the bar itself the right height is the parent `QBoxLayout`
    re-asking `heightForWidth()` on every layout pass, and a resize is one -- so
    the drag alone is enough for the GEOMETRY, which is all this test looks at.
    The bar's MINIMUM is a separate question with a separate answer
    (`FlowBar.resizeEvent`, and
    `test_a_wrap_reaches_the_tabs_own_minimum_without_a_restyle` is where that
    is asserted); removing `updateGeometry()` leaves this test green, and saying
    so here is the point -- it is what stopped this docstring citing a mechanism
    it does not exercise.

    Both directions, because a bar that re-stated its height on the way down and
    never on the way back would hold a two-line floor forever and take a row of
    whatever is under it on every window.
    """
    host, bar = _a_flow_bar_in_a_tight_column(THE_SIX_ON_THE_MODULES_BAR, width=1400, spare=-40)
    flow = bar.flow()
    assert len(flow._lines(bar.width())) == 1, "1400px already wrapped, so the drag proves nothing"
    one_line = bar.height()

    host.resize(700, host.height())
    process_events()

    assert len(flow._lines(bar.width())) > 1, "700px did not wrap the bar"
    assert bar.height() >= flow.heightForWidth(bar.width()), (
        f"the drag wrapped the bar and it was left {bar.height()}px tall, against the "
        f"{flow.heightForWidth(bar.width())} its lines now take"
    )

    host.resize(1400, host.height())
    process_events()

    assert len(flow._lines(bar.width())) == 1
    assert bar.height() <= one_line, (
        f"the bar came back to one line and kept {bar.height()}px of the two, against the "
        f"{one_line} it started at"
    )
    host.hide()


def test_a_flow_bar_puts_a_single_lines_leftover_at_the_gap(qapp: object) -> None:
    """`add_gap()`, which is the `addStretch(1)` the Modules bar used to carry.

    The bar reads as "the two that only READ this install" and then "the four
    that change it", and on one line that grouping is a gap between the second
    button and the third. Asserted as the gap being WIDER than the ordinary
    spacing on a wide bar and gone on a wrapped one -- the second half matters,
    because a gap that survived wrapping would push the last button of a line
    off the end.
    """
    from PySide6.QtWidgets import QPushButton, QWidget

    from yulon.ui.widgets.flow_layout import FLOW_SPACING, flow_bar

    host = QWidget()
    bar = flow_bar(host)
    flow = bar.flow()
    for index, label in enumerate(THE_SIX_ON_THE_MODULES_BAR):
        if index == 2:
            flow.add_gap()
        flow.addWidget(QPushButton(label, bar))
    host.resize(2000, 200)
    host.show()
    bar.resize(2000, flow.heightForWidth(2000))
    process_events()

    buttons = [flow.itemAt(i).widget() for i in range(flow.count())]
    assert len({b.y() for b in buttons}) == 1, "2000px wrapped the bar, so there is no leftover"
    at_the_gap = buttons[2].x() - (buttons[1].x() + buttons[1].width())
    elsewhere = buttons[1].x() - (buttons[0].x() + buttons[0].width())
    assert elsewhere == FLOW_SPACING, f"ordinary spacing is {elsewhere}, not {FLOW_SPACING}"
    assert at_the_gap > elsewhere, (
        f"the gap is {at_the_gap}px, the same as the spacing between any two buttons, so a "
        "2000px bar is not putting its leftover width there"
    )

    bar.resize(700, flow.heightForWidth(700))
    process_events()
    buttons = [flow.itemAt(i).widget() for i in range(flow.count())]
    assert len({b.y() for b in buttons}) > 1, "700px did not wrap the bar"
    for button in buttons:
        assert button.x() + button.width() <= bar.width(), (
            f"{button.text()!r} runs off the end of a wrapped bar: it ends at "
            f"{button.x() + button.width()} in {bar.width()}px"
        )
    host.hide()


def test_a_locked_row_keeps_its_reason_on_screen_at_every_width(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T75's handheld promise, asked of every width the window can be dragged to.

    T75 said the lock survives down to 1280x800 and meant it: below that the
    row's status column is narrower than a lock chip plus the overflow mark, and
    the strip dropped the lock. Measured themed at 960 on the shipped WotLK
    catalog, `accountwide` drew NO chip at all -- the `…` and nothing else -- so
    the one sentence saying why its Install is dead was behind a hover on the
    row most in need of it.

    Swept from `main.MINIMUM_WINDOW_SIZE` to 3000 rather than asked at 960
    alone, because the strip's rule changes shape twice across that range (the
    lock displaces the mark, then the lock itself has to be elided) and a single
    width only ever exercises one of them.

    Three assertions, and the last two are what keep the first honest. A lock
    drawn is not a lock READ: it may have been given a width its label does not
    fit, and the promise then is that the whole sentence is one hover away. And
    the sweep must really reach the narrow case -- a fix that made the strip
    wider instead would satisfy "the lock is drawn" at every width while leaving
    the rule it was meant to add untested, so the elided case is asserted to
    have happened at least once.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")

    locked = [row for row in view.modules_panel.rows() if row.data.install_reason is not None]
    assert locked, "no row on the shipped WotLK catalog has a locked Install"
    # The row the ticket was filed about, by name, because a sweep over "every
    # locked row" is satisfied by a catalog whose lock chips all happen to be
    # short today. `accountwide`'s is the longest one shipped.
    assert any(row.data.id == "accountwide" for row in locked), "`accountwide` is no longer locked"

    gone: list[str] = []
    silent: list[str] = []
    elided_somewhere = False
    for width in range(main.MINIMUM_WINDOW_SIZE[0], 3001, 40):
        _at(window, (width, 800))
        for row in locked:
            lock = next(chip for chip in row.data.chips if chip.detail == row.data.install_reason)
            strip = row.chip_strip
            if lock.label not in strip.visible_chip_labels():
                gone.append(f"{width}: {row.data.id} hid {lock.label!r}")
                continue
            if lock.label not in strip.elided_chip_labels():
                continue
            elided_somewhere = True
            button = next(
                b for b, chip in zip(row.chip_buttons, row.data.chips, strict=True) if chip is lock
            )
            # The whole refusal, and it is matched against `install_reason` --
            # the sentence the APPLIER would refuse with -- rather than against
            # a string spelled here, so the chip and the refusal cannot drift.
            if row.data.install_reason not in button.toolTip():
                silent.append(f"{width}: {row.data.id}'s elided lock says nothing on hover")
    assert gone == [], f"the reason Install is locked left the screen at {gone[:3]}"
    assert silent == [], f"an elided lock with the sentence nowhere: {silent[:3]}"
    assert elided_somewhere, (
        "no lock chip was ever drawn narrower than its label in the whole sweep, so the "
        "elision this test is about was never exercised"
    )


def test_a_narrow_row_spends_the_overflow_mark_on_its_lock(qapp: object) -> None:
    """The RULE behind the sweep, asked of a row carrying all eight chips.

    The catalog's own locked rows carry two chips at most, so they cannot say
    what the strip does when the lock is one of six things competing for the
    width. This one is narrowed until only the mark would have fitted, and what
    must happen is that the mark is not drawn at all: it is the one chip whose
    whole job is to say others exist, and a lock is the one chip that has to be
    on screen, so below the width that holds both, the mark is what goes.

    Which makes the mark's tooltip somebody else's to carry, and that is the
    other half asserted here -- every chip the width hid is still findable, on
    the lock instead of on the mark. Dropping that line leaves a row that has
    silently stopped mentioning three of its chips, and the assertion above it
    stays green.
    """
    chips = _every_chip()
    _host, row = _a_row_carrying(chips, width=700)
    strip = row.chip_strip
    lock = modules_panel.chip_needs_label("Playerbots")

    assert (
        lock in strip.visible_chip_labels()
    ), f"the lock is not drawn on a strip this narrow: {strip.visible_chip_labels()}"
    assert lock not in strip.elided_chip_labels(), (
        "this width is narrow enough to ELIDE the lock, so it is exercising the step below "
        "the one this test names; the mark going is what happens first"
    )
    assert strip.overflow is not None
    assert not strip.overflow.isVisibleTo(
        strip
    ), "the '…' took the room the lock needed; it is the chip that must go first"
    hidden = strip.hidden_chips()
    assert hidden, "700px fitted every chip, so nothing was displaced and this proves nothing"
    button = next(b for b, chip in zip(row.chip_buttons, chips, strict=True) if chip.label == lock)
    missing = [
        chip.label
        for chip in hidden
        if chip.label not in button.toolTip() or chip.detail not in button.toolTip()
    ]
    assert missing == [], f"the mark is gone and nothing took over what it said: {missing}"
    # And the lock's own sentence is still the first thing on that tooltip: it
    # is the reason the chip exists, and a tooltip that opened with somebody
    # else's chips would have buried it.
    needs = next(chip for chip in chips if chip.label == lock)
    assert button.toolTip().startswith(needs.detail)


def test_a_strip_dragged_narrow_and_back_stops_eliding(qapp: object) -> None:
    """The lock is drawn short at 360px and whole again at 3000, on the same widget.

    A row lives in a list a user drags, so every rule here is applied twice: once
    on the way down and once on the way back. This is the half that is easy to
    leave out -- an implementation that shortened the label by SETTING it would
    pass every assertion above and keep the shortened label for good, because the
    size hint it is measured against is computed from the text it was given.
    """
    chips = _every_chip()
    host, row = _a_row_carrying(chips, width=360)
    strip = row.chip_strip
    lock = modules_panel.chip_needs_label("Playerbots")
    assert lock in strip.elided_chip_labels(), "the lock fits at 360px, so nothing is proved"

    host.resize(3000, 400)
    process_events()

    assert strip.elided_chip_labels() == (), f"still elided at 3000px: {strip.elided_chip_labels()}"
    assert strip.visible_chip_labels() == tuple(chip.label for chip in chips)
    assert strip.hidden_chips() == ()
    needs = next(chip for chip in chips if chip.label == lock)
    button = next(b for b, chip in zip(row.chip_buttons, chips, strict=True) if chip is needs)
    assert (
        button.toolTip() == needs.detail
    ), "the lock is still carrying the overflow mark's tooltip at a width that draws the mark"
    assert button.text() == needs.label, "a chip button's text is not the chip's own label"


def test_a_pinned_lock_draws_an_elided_label_and_keeps_its_own(qapp: object) -> None:
    """What a narrowed lock chip PAINTS, and what it still answers when asked.

    Two different questions and the split between them is the design. `text()`
    is the chip's whole label at every width, because `RowWidget.chip_buttons` is
    published and read by the row's own menu and by half a dozen tests, and a
    button whose text depended on today's geometry would make every one of those
    readers a question about the window. `drawn_text()` is the other one, and it
    is what `paintEvent` puts on screen.

    Asserted as a PREFIX of the label plus Qt's own ellipsis rather than as a
    literal string: how many characters fit is the font's business, and a test
    that spelled `needs AzerothCore Lua Eng…` would fail on any honest change to
    a font while proving nothing extra.

    And the mark is checked to be absent, because a strip that drew both would
    have squeezed the lock for a reason this test cannot see.
    """
    chips = _every_chip()
    _host, row = _a_row_carrying(chips, width=400)
    strip = row.chip_strip
    needs = next(
        chip for chip in chips if chip.label == modules_panel.chip_needs_label("Playerbots")
    )
    button = next(b for b, chip in zip(row.chip_buttons, chips, strict=True) if chip is needs)

    assert needs.label in strip.elided_chip_labels(), (
        f"the lock is not being squeezed at 400px, so this proves nothing: "
        f"{strip.elided_chip_labels()}"
    )
    assert button.text() == needs.label, "the chip's own label is gone, not just its drawing"
    drawn = button.drawn_text()
    assert (
        drawn != needs.label
    ), "the button paints its whole label into a width that cannot hold it"
    assert drawn.endswith("…"), f"the label was cut rather than elided: {drawn!r}"
    assert needs.label.startswith(
        drawn[:-1]
    ), f"what is drawn is not the start of the label: {drawn!r}"
    # Measured against the style's own text area, asked for HERE rather than read
    # back out of `text_room()`: an assertion that used the function under test
    # to say how much room there was would hold whatever that function returned
    # -- it stayed green against `text_room()` answering the button's whole
    # width, which puts the last characters under the border.
    from PySide6.QtWidgets import QStyle, QStyleOptionButton

    option = QStyleOptionButton()
    button.initStyleOption(option)
    area = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
    assert (
        button.fontMetrics().horizontalAdvance(drawn) <= area.width()
    ), f"{drawn!r} is wider than the {area.width()}px the style gives this button for text"
    assert needs.detail in button.toolTip(), "an elided lock with its sentence nowhere"


def test_a_chip_that_fits_is_painted_by_qt_itself(qapp: object) -> None:
    """The other side of `drawn_text()`, and what stops it being always-elide.

    A `drawn_text()` that returned a shortened string at every width would pass
    the test above and would put `Rebuild pendin…` on a 4K screen. At a width
    that fits, what is drawn is exactly what the chip says, for every chip on
    the row -- which is also the assertion that `text_room()`'s chrome
    arithmetic is not quietly one pixel short.
    """
    chips = _every_chip()
    _host, row = _a_row_carrying(chips, width=3000)

    painted = {chip.label: b.drawn_text() for b, chip in zip(row.chip_buttons, chips, strict=True)}
    assert painted == {
        chip.label: chip.label for chip in chips
    }, f"a chip is drawn shortened at a width that fits all eight: {painted}"


def test_a_failed_jobs_folded_strip_says_what_failed_on_one_line(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T80's strip, at the width gate A2 was taken in and after a job that FAILED.

    A refusal on these tabs is `str(exc)` -- one paragraph -- and the strip's
    status field used to WRAP it. Folded, the strip is the whole panel, so those
    extra lines were the panel's whole height and the tab had nothing spare to
    give them: the gate caught a three-line sentence with its third line cut in
    half.

    Four assertions. The first two are the defect, and they are asked of
    `heightForWidth()` -- how tall the field's own text is at the width it is
    really drawn in -- against the font's line spacing. Not against the field's
    HEIGHT, which is 50px either way because the field is stretched to the row
    that holds the Stop button, and not against the panel's `minimumSizeHint()`,
    which is a wrapping `QLabel`'s small one and does not move: both were tried
    and both stayed green against the wrapping label this replaced (measured:
    the refusal needs 56px of the 50 it has at 285px wide, four lines where
    there is room for three and a half -- which is the half-line the gate
    photographed).

    The last two are what stop that being met by a field that simply keeps
    less: the whole refusal is still what the panel reports and what a hover
    shows.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, (1000, 700))
    refusal = (
        "that compose file was not written by Yu'lon, so this app will not rebuild the "
        "server it describes; move it aside and install again, and nothing here is touched."
    )
    assert view.rebuild_log.run(lambda: _raise(RuntimeError(refusal)), title="rebuild") is True
    pump_until(lambda: not view.rebuild_log.running, "the failed job finished")
    process_events()
    view.rebuild_log.set_collapsed(True)
    process_events()

    field = view.rebuild_log._status
    assert (
        refusal in view.rebuild_log.status_text()
    ), f"the panel no longer reports what failed: {view.rebuild_log.status_text()!r}"
    assert refusal in field.toolTip(), "the refusal is neither on screen in full nor on hover"
    needed = field.heightForWidth(field.width())
    one_line = field.fontMetrics().lineSpacing()
    assert needed <= one_line, (
        f"the folded strip's sentence needs {needed}px at the {field.width()}px it is drawn "
        f"in -- {needed / one_line:.1f} lines of {one_line}px -- so it is wrapping"
    )
    assert (
        needed <= field.height()
    ), f"the field is {field.height()}px and its text needs {needed}: the bottom line is cut"
    # One line is not enough on its own: a field that kept the whole paragraph
    # on one line and let the style cut it at the edge measures the same height
    # and says nothing about having been cut. What is DRAWN has to fit, and it
    # has to be elided rather than chopped -- the ellipsis is the only mark on
    # screen that tells the reader there is more to hover for.
    drawn = field.text()
    assert drawn != refusal, "the whole paragraph is on one line, so it is being cut at the edge"
    assert drawn.endswith("…"), f"the sentence was chopped rather than elided: {drawn!r}"
    assert (
        field.fontMetrics().horizontalAdvance(drawn) <= field.width()
    ), f"{drawn!r} is wider than the {field.width()}px field it is drawn in"
    assert view.rebuild_log.height() >= view.rebuild_log.minimumSizeHint().height(), (
        f"the folded log is CLIPPED at {view.rebuild_log.height()}px against the "
        f"{view.rebuild_log.minimumSizeHint().height()} it says it needs"
    )


def _raise(exc: Exception) -> Iterator[str]:
    """A line source that fails the way a refusing job fails: it raises."""
    yield "starting"
    raise exc


LOG_OPEN_WIDTHS = ((1000, 700), (960, 600))
"""The two windows the Modules tab is measured at with a job's output in the log.

1000x700 is the window gate A2 was taken in and the one this case was found in;
960x600 is `main.MINIMUM_WINDOW_SIZE`. Both are here because they fail
differently: 1000x700 has room for everything except the last 13px, and 960x600
is 112px short -- a fix sized to either one alone misses the other.
"""


def _drawn_under_their_minimum(tab: Any) -> list[str]:
    """Every widget on `tab` drawn shorter than it says it needs.

    `_squeezed()` above asks the same question and is kept separate on purpose:
    that one is called at sizes where the tab is expected to fit outright, and
    this one at sizes where something has to give -- the assertion is that what
    gives is a widget's own choice (the log folds) and never Qt's proportional
    cut, which takes the bottom off whatever is in the way.
    """
    from PySide6.QtWidgets import QVBoxLayout

    box = tab.layout()
    assert isinstance(box, QVBoxLayout)
    return [
        f"{type(w).__name__}: {w.height()} < {w.minimumSizeHint().height()}"
        for w in (box.itemAt(i).widget() for i in range(box.count()))
        if w is not None and w.isVisible() and w.height() < w.minimumSizeHint().height()
    ]


def test_a_job_leaves_nothing_on_the_modules_tab_cut_at_the_narrow_windows(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The wrapped bar's second line must not be paid for out of the log's text pane.

    T83 round 1's defect, and it is what a minimum that under-reports buys.
    `FlowLayout.minimumSize()` answered one line at every width, so with the bar
    wrapped at 1000x700 the tab's own layout claimed it needed 482px when the
    real need was 538 -- and `QBoxLayout`, told the tab fitted, drew
    `_IdleLogPanel` at 153px of the 180 it needs and cut its text pane. That is
    the state T80's cap docstring exists to prevent, and it came back one ticket
    later through a widget that had nothing to do with logs.

    Three assertions, each of which the other two are satisfied without:

    * nothing on the tab is drawn under its own minimum, which is the defect;
    * every toolbar label is still whole, so the height cannot be found by
      un-wrapping the bar and clipping the text again;
    * and the log is USABLE at whatever height it ended up with -- folded is
      fine, because a folded `LogPanel` is its strip and the strip carries the
      job's status line and Stop; drawn-but-cut is not, and the two are a few
      pixels apart on screen.

    Run AFTER a job, because that is the only state in which this tab has an
    open log at all: `_IdleLogPanel` starts folded and it is `run_started` that
    asks for the room (T80).
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    assert not view.rebuild_log.collapsed, "the job did not open the log, so nothing is tested"

    for size in LOG_OPEN_WIDTHS:
        _at(window, size)
        assert _drawn_under_their_minimum(tab) == [], (
            f"something on the Modules tab is drawn under its own minimum at {size} with "
            f"the log open: {_drawn_under_their_minimum(tab)}"
        )
        clipped = [why for why in (_clipped(b) for b in _module_toolbar_buttons(view)) if why]
        assert clipped == [], f"toolbar text cut off at {size}: {clipped}"
        assert _bar_holds_every_line(view) == [], (
            f"a toolbar button is drawn outside the action bar at {size}: "
            f"{_bar_holds_every_line(view)}"
        )
        log = view.rebuild_log
        assert log.height() >= log.minimumSizeHint().height(), (
            f"the log is drawn at {log.height()}px against the {log.minimumSizeHint().height()} "
            f"it needs at {size}: it was cut rather than folded"
        )
        assert log.status_text() != "", f"the log's strip is empty at {size}"


def test_the_modules_log_takes_its_height_back_when_the_window_grows(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The fold the panel does for room is undone when the room comes back.

    The other half of the test above, and the one that says the fold is a
    response to a width rather than a one-way door. It is easy to get wrong in
    exactly one way: a panel that remembers "I folded myself" and clears that
    memory on its own `set_collapsed()` never re-opens -- measured, with the log
    left folded at 1280x800 where there were 312px of room for it.

    And the last assertion is the other direction: a fold the USER asked for
    survives a window that grows, or the app re-opens a panel somebody has just
    put away.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)

    _at(window, main.MINIMUM_WINDOW_SIZE)
    assert view.rebuild_log.collapsed, (
        f"the log is open at {main.MINIMUM_WINDOW_SIZE}, where the tab has "
        f"{view.rebuild_log.height()}px for the {view.rebuild_log.minimumSizeHint().height()} "
        "an open one needs"
    )

    _at(window, main.DEFAULT_WINDOW_SIZE)

    assert not view.rebuild_log.collapsed, (
        "the log was folded to fit a narrow window and never came back at "
        f"{main.DEFAULT_WINDOW_SIZE}"
    )
    assert "Compile finished." in view.rebuild_log.text(), "the fold lost the job's output"

    _click(_the_handle_on(view.rebuild_log))
    assert view.rebuild_log.collapsed, "the handle did not fold the log"
    _at(window, (1920, 1080))

    assert view.rebuild_log.collapsed, (
        "a bigger window re-opened a log the user had folded; only a fold the PANEL did "
        "for room is its own to undo"
    )


def _what_the_tab_owes(tab: Any) -> int:
    """The height every widget on `tab` needs, added up the way its layout does.

    Recomputed from the children rather than read off the tab, because the whole
    question is whether the tab's OWN answer has kept up with them.
    """
    from PySide6.QtWidgets import QVBoxLayout

    box = tab.layout()
    assert isinstance(box, QVBoxLayout)
    margins = box.contentsMargins()
    owed = margins.top() + margins.bottom()
    shown = 0
    for index in range(box.count()):
        item = box.itemAt(index)
        widget = item.widget()
        if widget is not None and not widget.isVisible():
            continue
        shown += 1
        owed += widget.minimumSizeHint().height() if widget is not None else 0
    return owed + box.spacing() * max(0, shown - 1)


def test_a_wrap_reaches_the_tabs_own_minimum_without_a_restyle(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The bar wrapping has to move the TAB's minimum, and a plain resize is the test.

    A parent `QBoxLayout` works its children's minimums out when it is
    invalidated and re-uses them until a child says they have moved.
    `FlowLayout.minimumSize()` answers for the width the layout was last GIVEN,
    so a bar that wraps has a new minimum and no way to say so --
    `FlowBar.resizeEvent`'s `updateGeometry()` is the saying.

    **Resized without `_at()`, and that is the whole point.** `_at()` re-applies
    the theme, which invalidates every layout on the window and makes the parent
    re-ask anyway; so does a real drag, most of the time
    (`main._Window._restyle_for_width`). Measured through `_at()`, removing
    `updateGeometry()` changes nothing and reads as dead code -- it was removed
    once on exactly that evidence. Through a plain `resize()` the tab's minimum
    stays at the 376px it had with the bar on one line while the bar itself says
    106, and everything downstream -- `_IdleLogPanel`'s room, and so whether the
    log is folded or cut -- is decided from that stale number.

    Two assertions: the tab's minimum went UP by at least a line when the bar
    wrapped, and it is at least what its children add up to. The second is the
    invariant and the first is what makes it non-vacuous -- a tab whose minimum
    was already generous would satisfy the second at both widths.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    bar = view.module_actions

    window.resize(1400, 800)
    process_events()
    assert len(bar.flow()._lines(bar.width())) == 1, "1400px already wrapped the bar"
    on_one_line = tab.minimumSizeHint().height()
    line = bar.flow()._line_height()

    window.resize(960, 800)
    process_events()

    assert len(bar.flow()._lines(bar.width())) > 1, "960px did not wrap the bar"
    wrapped = tab.minimumSizeHint().height()
    assert wrapped >= on_one_line + line, (
        f"the bar wrapped and the tab's minimum went {on_one_line} -> {wrapped}, less than the "
        f"{line}px line it gained: the tab is still being laid out against the old one"
    )
    assert wrapped >= _what_the_tab_owes(tab), (
        f"the tab says it needs {wrapped}px and its own children add up to "
        f"{_what_the_tab_owes(tab)}"
    )


A_REFUSAL_IN_THE_REPORT_BOX = (
    "mod-city-bots: FAILED. the applier refused: mod-playerbots is not installed, and this "
    "module declares it in requires. Install it first, then press Install again."
)
"""What the Modules report box holds after any press that did not work.

A paragraph and not a word, because the box is sized to its TEXT: an empty box
is folded away by its strip (T80) and a one-word one is a line, so a fixture
that put `no` in it would be measuring a tab that still had the height. Wrapped
at these widths this is what `_module_failed` really leaves behind.
"""


def _counting_folds(panel: Any) -> tuple[list[str], Callable[[], None]]:
    """Watch `panel`'s real fold transitions; returns the log and a restore.

    Patched on the CLASS and not the instance, because the calls being counted
    are the panel's own `self.set_collapsed(...)` -- an instance attribute would
    be found by those too, but so would any other panel of the same type in the
    same test, and counting one widget's flicker is the whole point. Only a call
    that CHANGES the state is recorded: `set_collapsed` is a documented no-op
    otherwise, and counting no-ops would make this a test of how often the
    method is reached rather than of what the user sees.
    """
    kind = type(panel)
    real = kind.set_collapsed
    seen: list[str] = []

    def counting(self: Any, collapsed: bool) -> None:
        if self is panel and collapsed != self.collapsed:
            seen.append("fold" if collapsed else "open")
        real(self, collapsed)

    kind.set_collapsed = counting  # type: ignore[method-assign]
    return seen, lambda: setattr(kind, "set_collapsed", real)


def test_a_populated_report_leaves_nothing_on_the_modules_tab_cut(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The state after ANY press on this tab, at the two narrow windows.

    Round 2 folded the log and stopped there, and the tab still did not fit: a
    report box is 90px of minimum and it is populated the moment anything on
    this tab is pressed. Measured with the log already folded -- at 1000x700 the
    custom-module card was drawn 109 of the 122 it needs, and at 960x600 the
    ACTION BAR was drawn 74 of its 106, its second row of buttons sliced through
    horizontally, which is this ticket's own defect arriving from underneath.

    The order is what is asserted, not just the absence of clipping: the things
    that may give are the two that fold to a strip and the list that scrolls,
    and a "fix" that met the first assertion by letting the card or a toolbar
    line be short would be the bug with a passing test.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    view.module_report.setPlainText(A_REFUSAL_IN_THE_REPORT_BOX)
    process_events()
    assert not view.module_report_strip.collapsed, "the report box did not open for its text"
    assert not view.rebuild_log.collapsed, "the job did not open the log"

    for size in LOG_OPEN_WIDTHS:
        _at(window, size)
        assert _drawn_under_their_minimum(tab) == [], (
            f"something on the Modules tab is drawn under its own minimum at {size} with a "
            f"report in the box: {_drawn_under_their_minimum(tab)}"
        )
        clipped = [why for why in (_clipped(b) for b in _module_toolbar_buttons(view)) if why]
        assert clipped == [], f"toolbar text cut off at {size}: {clipped}"
        assert _bar_holds_every_line(view) == [], (
            f"a toolbar button is drawn outside the action bar at {size}: "
            f"{_bar_holds_every_line(view)}"
        )
        # The report is still REACHABLE where it has been folded for room: the
        # strip names it and one press brings it back, which is the difference
        # between a box that gave its height up and a box that was cut.
        assert view.module_report_strip.isVisible(), f"the report's strip is gone at {size}"
        assert (
            view.module_report.toPlainText() == A_REFUSAL_IN_THE_REPORT_BOX
        ), f"folding the report for room at {size} lost what it said"


def test_the_report_box_is_what_gives_after_the_log_and_before_the_list(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The ORDER, asked as three questions the absence of clipping does not ask.

    A tab that folded everything at every width would pass the test above, and
    so would one that took the height out of the list first and left the user
    with two rows on a window that could have shown six. What is asserted is
    that each step is only taken when the one before it was not enough:

    * at the smallest window with a report in the box AND a rebuild owed, BOTH
      boxes are folded and the list is at its own floor -- the narrowest case,
      where all three steps are needed;
    * at 1280x800 NEITHER is folded and the list floor is back, so none of this
      is a one-way door;
    * and the list never gives while a box is still open, which is the order
      itself.

    The banner is what makes the first case reach step 3 at all since T85: the
    minimum window went up to hold the wrapped bar and the card whole with one on
    screen, and without one the smallest window now fits with the list at its
    full `MODULE_LIST_MIN_HEIGHT`.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    view.module_report.setPlainText(A_REFUSAL_IN_THE_REPORT_BOX)
    _a_rebuild_is_owed(view)
    process_events()

    _at(window, main.MINIMUM_WINDOW_SIZE)
    assert view.rebuild_log.collapsed, "the log is open at the smallest window"
    assert view.module_report_strip.collapsed, "the report is open at the smallest window"
    assert view.modules_panel.minimumHeight() < controller_view_module.MODULE_LIST_MIN_HEIGHT, (
        "both boxes are folded and the tab STILL does not fit at this size -- the action bar "
        "takes two lines, the card needs 136px of wrapped sentence and the banner 62 -- so the "
        f"list's floor is the one thing left to give: it is still "
        f"{view.modules_panel.minimumHeight()} of {controller_view_module.MODULE_LIST_MIN_HEIGHT}"
    )
    # Strictly less, and that is a claim about the shipped catalog and theme at
    # this one size rather than about the rule. If a future theme makes the
    # smallest window fit outright this goes red, and that is the right red: the
    # ladder's last rung would no longer be exercised anywhere, and this is the
    # test that says so rather than quietly covering nothing.
    #
    # `_LIST_FLOOR_FLOOR` and NOT the list's own `minimumSizeHint()`, which is
    # what this asserted until T85 on the belief that Qt ignores a minimum under
    # a widget's hint. It does not -- `qSmartMinSize` prefers an explicit
    # `minimumHeight()` at any value above zero -- and the hint was 70 where the
    # ladder needed 40, so the last rung was 30px shorter than it reads and the
    # shortfall went on to the toolbar and the card instead.
    assert view.modules_panel.minimumHeight() >= controller_view_module._LIST_FLOOR_FLOOR, (
        f"the floor was lowered to {view.modules_panel.minimumHeight()}, under the "
        f"{controller_view_module._LIST_FLOOR_FLOOR} that is the bottom of this ladder -- "
        "below it the honest answer is a tab that scrolls, not a shorter list"
    )

    _a_rebuild_is_not_owed(view)
    _at(window, main.DEFAULT_WINDOW_SIZE)

    assert not view.rebuild_log.collapsed, "the log never came back at the default window"
    assert not view.module_report_strip.collapsed, "the report never came back"
    assert view.modules_panel.minimumHeight() == controller_view_module.MODULE_LIST_MIN_HEIGHT, (
        f"the list's floor is still {view.modules_panel.minimumHeight()} at a window with "
        f"room for the {controller_view_module.MODULE_LIST_MIN_HEIGHT} it asks for"
    )

    # And the order: wherever the list has given anything, both boxes are away.
    for width in range(960, 1400, 20):
        _at(window, (width, 700))
        if view.modules_panel.minimumHeight() < controller_view_module.MODULE_LIST_MIN_HEIGHT:
            assert view.rebuild_log.collapsed and view.module_report_strip.collapsed, (
                f"at {width}px the list gave height while a box was still open: log "
                f"folded={view.rebuild_log.collapsed}, report "
                f"folded={view.module_report_strip.collapsed}"
            )


THE_WRAPPED_WIDTHS = tuple(range(960, 1120, 10))
"""Every width at the minimum window's height where the action bar takes two lines.

960 is `main.MINIMUM_WINDOW_SIZE`'s width and 1120 is where the bar goes back to
one line; between them the theme's font grows with the width while the bar still
wraps, so the tab wants MORE height as the window gets wider. Swept rather than
asked at 960 because the worst case is not at either end: with the rebuild banner
up, the smallest height at which nothing is cut is 634 at 960 and 637 at 1090,
and a test that asked only at the minimum width would have passed on a 634 that
clips the custom-module card thirteen widths later (T85).

Every ten pixels rather than every twenty, so that 1090 is really asked: a
twenty-pixel step from 960 lands on 1080 and 1100 and steps over the worst one.
"""


def _cut_on_the_modules_tab(view: ControllerView, tab: Any) -> list[str]:
    """Everything on the Modules tab that is drawn too short to read, which must be none.

    Four questions, and the list is exempt from the first of them BY DESIGN: it
    is the one widget here that is complete at any height because it scrolls, and
    `_TabFit`'s last rung takes it under its own `minimumSizeHint()` on purpose.
    Every other widget drawn under its minimum is text somebody cannot read.

    The card's BUTTONS are asked for separately from the card's height because
    they fail separately: `QGroupBox` is happy to be drawn shorter than its own
    layout and lets the children hang out of the bottom of the frame, which is
    what gate round 6 photographed -- two buttons below the card's own edge, on a
    card whose height on its own looked only a little short.
    """
    from PySide6.QtWidgets import QGroupBox, QPushButton

    cut = [
        why
        for why in _drawn_under_their_minimum(tab)
        if not why.startswith(type(view.modules_panel).__name__)
    ]
    cut += [why for why in (_clipped(b) for b in _module_toolbar_buttons(view)) if why]
    cut += _bar_holds_every_line(view)
    for card in tab.findChildren(QGroupBox):
        for button in card.findChildren(QPushButton):
            if button.y() < 0 or button.y() + button.height() > card.height():
                cut.append(
                    f"{button.text()!r} ends at {button.y() + button.height()}px of the "
                    f"{card.height()}px card {card.title()!r}"
                )
    return cut


def test_the_smallest_window_draws_the_toolbar_and_the_card_whole_with_a_rebuild_owed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T85's first half: the size the app PROMISES, in the state gate 6 found it in.

    The banner is the whole of what was new. It costs this tab 62px plus the
    spacing above it, it is on screen for as long as it takes a user to press
    Rebuild, and at the 960x600 the app used to allow it put the tab 82px past
    what `_TabFit`'s whole ladder could give: the wrapped action bar was drawn
    82px of its 106 with `Rebuild the server…` sliced through its own border, and
    the custom-module card 82 of 122 with both its buttons hanging below the
    frame. `QBoxLayout` shares a shortfall over every child, so the two widgets
    whose only way of being shorter is to cut the words in them paid it.

    Two things had to change and this test needs both. The ladder's last rung was
    30px shorter than it read -- `_TabFit` floored the list at its own
    `minimumSizeHint()` of 70 on the belief that Qt ignores anything under it,
    and Qt does not -- and even with the list at `_LIST_FLOOR_FLOOR` the tab was
    48px short, so `MINIMUM_WINDOW_SIZE` went from 600 to 640.

    Swept across `THE_WRAPPED_WIDTHS` rather than asked at the minimum, because
    the worst case is at 1090 and not at 960: see that constant.

    After a job AND with a report in the box, which is the state that puts every
    rung of the ladder under load at once.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    _a_rebuild_is_owed(view)
    assert view.module_report.toPlainText() != "", "the report box is empty, so a rung is unloaded"

    for width in THE_WRAPPED_WIDTHS:
        _at(window, (width, main.MINIMUM_WINDOW_SIZE[1]))
        assert (
            view.rebuild_banner.isHidden() is False
        ), f"the banner left the screen at {width}px, so this is not the case under test"
        assert (
            len({b.y() for b in _module_toolbar_buttons(view)}) > 1
        ), f"the action bar is one line at {width}px, so the hard case is not being measured"
        assert _cut_on_the_modules_tab(view, tab) == [], (
            f"something on the Modules tab is cut at {width}x{main.MINIMUM_WINDOW_SIZE[1]} with a "
            f"rebuild owed: {_cut_on_the_modules_tab(view, tab)}"
        )


def test_the_logs_minimum_in_the_state_it_is_not_in_is_the_one_it_really_has(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`open_minimum()` folded must equal what the panel really needs open, and back.

    Those two methods exist because `_TabFit` has to ask an open panel what it
    would need folded and a folded one what it would need open -- that IS the
    question it is deciding -- so both are DERIVED from the state the panel is
    not in. A derivation that is 16px light is a tab that finds room it does not
    have: it opens the log, is asked again with the honest number now that the
    pane is showing, and folds it. One resize, two transitions, and the panel
    ends up where it started with a flash in between.

    Sixteen is not a guess. The theme gives every `QPlainTextEdit` a 90px
    `min-height` and the pane really holds back 106, and a layout item's minimum
    is the larger of the widget's hint and its explicit `minimumHeight()` --
    `_ReportStrip.box_minimum()` already carries that scar (T45), and
    `_IdleLogPanel` was written next to it with the hint alone.

    Asserted as an EQUALITY between two numbers computed by different code: the
    panel's arithmetic on one side and Qt's own laid-out answer on the other. A
    test that recomputed the derivation would agree with itself whatever it said.

    Maximised, so neither answer is a cap's or a shortfall's.
    """
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    log = view.rebuild_log

    assert log.collapsed, "the log did not start folded, so `open_minimum()` is not derived here"
    derived_open = log.open_minimum()

    _ran_a_job(view)
    assert not log.collapsed, "the job did not open the log"
    really_open = log.minimumSizeHint().height()
    derived_folded = log.folded_minimum()

    assert derived_open == really_open, (
        f"folded, the panel said an open one needs {derived_open}px; open, it needs "
        f"{really_open}. The tab decides whether to unfold from the first number"
    )

    _click(_the_handle_on(log))
    assert log.collapsed, "the handle did not fold the log"
    really_folded = log.minimumSizeHint().height()

    assert derived_folded == really_folded, (
        f"open, the panel said a folded one needs {derived_folded}px; folded, it needs "
        f"{really_folded}"
    )


THE_CLIENT_INSIDE_A_1280x800_FRAME = (1252, 734)
"""What `main.DEFAULT_WINDOW_SIZE` leaves the app when the number is the FRAME.

`xdotool search` returns mutter's frame rather than the client, and on the box
gate round 6 ran on the frame is 28px wider and 66px taller than what the app
gets. Round 6 corrected for it and sized every shot against the client, so the
1280x800 in T85 really is a 1280x800 client -- and this size is asserted beside
it because the correction is one line of a recipe and the failure mode is
silent: a run that sizes the frame instead is measuring a 554px tab where the
test measures 620, which is the difference between an open log needing the
report to give and needing the report AND the list's floor.
"""


def test_a_press_on_the_log_reopens_it_at_the_window_the_app_opens_at(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T85's second half: the fold that went one way, and the chevron that did nothing.

    Live at 1280x800 with a rebuild owed, a log left open while maximised folded
    itself on the way down and its chevron was then dead -- it came back only on
    a window big enough that nothing had to give. The banner is why: it costs
    this tab 52px at this width, and with it an open log and a populated report
    want 668px of a 620px tab. One of them has to fold, the published order sends
    the log, and pressing its handle did nothing at all, because `_apply()`
    derives the fold from `_has_room` and `set_room(False)` is a no-op when the
    answer has not changed.

    The answer is that a press REORDERS the ladder (`_TabFit._give_order`): the
    panel a person last asked for goes last, so the other one gives for it, and
    a press may spend the list's floor where folding the other one is not
    enough. Both directions are asserted, and the second is what stops this being
    "the log always wins" -- a press on the REPORT's strip takes it back, and
    neither panel is privileged over the other.

    Asked at two sizes: 1280x800, which is what gate round 6 photographed, and
    the 1252x734 a 1280x800 FRAME leaves the client on that box. At 620px of tab
    folding the report is enough; at 554 it leaves the log two pixels short, so
    the second case is the one that needs the list's floor as well.

    The last assertion is the one the live defect would still pass without: the
    log is not merely un-collapsed but drawn at the height it needs. A panel
    opened at a size that cannot hold it is a cut panel, which is what T83's
    round 3 measured when the press showed the box itself.
    """
    import main

    for size in (main.DEFAULT_WINDOW_SIZE, THE_CLIENT_INSIDE_A_1280x800_FRAME):
        view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
        window, _tab = _controller_in_the_real_window(view, "Modules")
        _at(window, DESKTOP_1080P)
        _ran_a_job(view)
        _a_rebuild_is_owed(view)
        assert not view.rebuild_log.collapsed, "the job did not open the log while maximised"

        _at(window, size)
        assert view.rebuild_log.collapsed, (
            f"the log is still open at {size} with a rebuild owed, so the press below is not "
            "the press this ticket is about"
        )

        _click(_the_handle_on(view.rebuild_log))

        assert not view.rebuild_log.collapsed, (
            f"the chevron did nothing: the log is still folded at {size}, where the tab can "
            "hold it once the report and the list's floor give"
        )
        assert view.module_report_strip.collapsed, (
            f"both are open at {size}, so nothing gave -- either the tab grew or this is "
            "measuring a size where the question does not arise"
        )
        assert view.rebuild_log.height() >= view.rebuild_log.minimumSizeHint().height(), (
            f"the log was opened at {view.rebuild_log.height()}px against the "
            f"{view.rebuild_log.minimumSizeHint().height()} it needs at {size}: open and cut"
        )
        assert "Compile finished." in view.rebuild_log.text(), "the fold lost the job's output"

        # And back the other way: the report's own strip takes the height back.
        _click(view.module_report_strip)

        assert not view.module_report_strip.collapsed, (
            f"a press on the report's strip did not reopen it at {size} -- the log is being "
            "privileged rather than the panel the user last asked for"
        )
        assert view.rebuild_log.collapsed, f"the report reopened without the log giving at {size}"


def test_folding_the_log_by_hand_gives_the_report_its_height_back(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The other side of the reorder: what the user puts AWAY costs the tab nothing.

    `_TabFit` used to ask "can this tab hold both panels open?" whatever anybody
    wanted, and that question has no answer that helps here. Once a press has put
    the log at the end of the give order, a tab asking it keeps refusing the
    report for a log that is folded and that nobody is asking for -- the user
    presses the log shut to get the report back and gets neither.

    So `settle()` starts from what is ASKED FOR (`wants_open()`) rather than from
    both panels open. The log folded by hand is not asked for, the sum drops by
    the whole of an open log, and the report fits with room over.

    Three presses, and the third is the assertion: the first proves the size is
    one where they do not both fit, the second is the reorder, the third is this.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    _a_rebuild_is_owed(view)
    _at(window, main.DEFAULT_WINDOW_SIZE)

    assert view.rebuild_log.collapsed and not view.module_report_strip.collapsed, (
        "the tab holds both at this size, so there is nothing for either press to cost: "
        f"log folded={view.rebuild_log.collapsed}, report "
        f"folded={view.module_report_strip.collapsed}"
    )

    _click(_the_handle_on(view.rebuild_log))
    assert not view.rebuild_log.collapsed, "the press did not open the log"
    assert view.module_report_strip.collapsed, "the report did not give for it"

    _click(_the_handle_on(view.rebuild_log))

    assert view.rebuild_log.collapsed, "the second press did not fold the log"
    assert not view.module_report_strip.collapsed, (
        "the report is still folded for a log the user has just put away: the room is being "
        "worked out from what the tab COULD hold rather than from what is asked for"
    )
    assert view.module_report.toPlainText() != "", "the report came back empty"


def test_a_restyle_at_an_unchanged_width_folds_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The flicker: a themed restyle used to unfold the log and refold it.

    Every height on this tab is a function of the theme, the theme is
    regenerated at every width the window settles at
    (`main._Window._restyle_for_width`), and a widget's size hint is stale until
    it has been polished. Decided inside the layout pass that asks for it, the
    fold was taken against half-updated numbers: the action bar's minimum still
    said one line, so the tab looked 56px roomier than it was, the log opened,
    the bar was re-laid, and the log folded again. Two transitions per settle at
    every width from 940 to 1110 -- a visible flash on every drag below the fold
    width, measured before `_TabFit`'s coalescing timer.

    Asserted as ZERO at an unchanged size and at most one per step across the
    sweep, for BOTH of the strips that fold for room. One is the honest bound for
    a step that really does cross a fold width: the state has to change once, and
    it is the SECOND transition that is the flicker.
    """
    from yulon.ui.theme import apply_dadcraft_theme

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    view.module_report.setPlainText(A_REFUSAL_IN_THE_REPORT_BOX)
    process_events()
    _at(window, (1000, 700))
    assert view.rebuild_log.collapsed, "1000x700 does not fold the log, so this proves nothing"
    assert view.module_report_strip.collapsed, "1000x700 does not fold the report either"

    # BOTH strips, because they are two objects running the same derivation and
    # a flicker in either is the same flash on the same tab. Counted with the
    # report POPULATED, which is also what gives the report strip a fold to
    # flicker: an empty box is folded by its text and never consulted about room.
    log_seen, restore_log = _counting_folds(view.rebuild_log)
    report_seen, restore_report = _counting_folds(view.module_report_strip)
    try:
        apply_dadcraft_theme(window, width=window.width())
        process_events()
        assert log_seen == [], f"a restyle at an unchanged size folded the log {log_seen}"
        assert report_seen == [], f"a restyle at an unchanged size folded the report {report_seen}"

        worst: list[str] = []
        moved = 0
        # Up to 1200 and not 1110: the report's own fold width is 1120 at this
        # height, and a sweep that stopped short of it counted a strip that
        # never moves -- which cannot flicker, so half of this test would have
        # been green about nothing. `moved` is what says so out loud.
        for width in range(940, 1201, 10):
            log_seen.clear()
            report_seen.clear()
            window.resize(width, 700)
            apply_dadcraft_theme(window, width=window.width())
            process_events()
            moved += len(log_seen) + len(report_seen)
            if len(log_seen) > 1:
                worst.append(f"{width}: log {log_seen}")
            if len(report_seen) > 1:
                worst.append(f"{width}: report {report_seen}")
        assert worst == [], f"a strip flickered during a drag at {worst[:3]}"
        assert moved, (
            "neither strip folded or unfolded anywhere in the sweep, so 'at most one "
            "transition per step' is a bound on nothing"
        )
    finally:
        restore_log()
        restore_report()


def test_opening_the_report_by_hand_cannot_cut_the_tab(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A press on a strip is a request, not a `setVisible()`.

    `_TabFit` folds the report for room and then answers the same way for as
    long as the width holds -- `set_room(False)` is a no-op when the answer has
    not changed -- so anything that shows the box behind its back stays shown.
    Until round 3 the strip's own handler did exactly that, and one click at
    960x600 with a refusal in the box drew the action bar at 76px of the 106 its
    two lines need, `Rebuild the server…` sliced through, and the custom-module
    card's label at 9px of 28.

    Either outcome is allowed and only one of them is asserted for, because the
    rule is about what must not happen: the box may stay folded (it does), or it
    may open with the list's floor giving the difference. What may not happen is
    the tab being cut. The click is a real mouse press on the strip, because the
    defect was in the handler that press reaches.

    The second half is the one that keeps this from passing for the wrong
    reason: the press must still be LIVE. A strip that had been made unclickable,
    or one that never folds at all, satisfies "nothing is cut" and is a
    different bug -- so the same press is made again at a width with the room,
    and there it opens.
    """
    import main

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    view.module_report.setPlainText(A_REFUSAL_IN_THE_REPORT_BOX)
    process_events()

    _at(window, main.MINIMUM_WINDOW_SIZE)
    assert view.module_report_strip.collapsed, "the report is not folded here, so no press to test"

    _click(view.module_report_strip)
    process_events()

    assert _drawn_under_their_minimum(tab) == [], (
        "a click on the report's strip at the smallest window cut the tab: "
        f"{_drawn_under_their_minimum(tab)}"
    )
    clipped = [why for why in (_clipped(b) for b in _module_toolbar_buttons(view)) if why]
    assert clipped == [], f"the click cut the toolbar's text: {clipped}"
    assert (
        _bar_holds_every_line(view) == []
    ), f"the click pushed a toolbar button outside the bar: {_bar_holds_every_line(view)}"

    _at(window, DESKTOP_1080P)
    was = view.module_report_strip.collapsed
    _click(view.module_report_strip)
    process_events()
    assert view.module_report_strip.collapsed is not was, (
        "the report's strip no longer answers a press at a window with the room for it, so "
        "the assertions above are about a dead control"
    )


def test_an_open_report_is_counted_at_the_height_the_layout_holds_back(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`_owed()` has to count the report box the way the layout counts it.

    A layout item's minimum is the larger of its widget's hint and any explicit
    `minimumHeight()`, and the theme puts one on every text field (T45). Counted
    by the hint alone the box came to 90 against the 106 the layout really holds
    back, so `_owed()` under-reported an open report by 16px -- and between 1030
    and 1080 wide that is the whole margin: the sum came to 522 against a tab of
    525 while the tab's real minimum was 538, the report stayed open, and the
    custom-module card was drawn 108 of its 122.

    1040x700 and not a sweep, because this is an arithmetic error with an exact
    window where it shows: wider and the card fits anyway, narrower and the
    report folds for other reasons. The card is named because it is the widget
    that pays -- it is the one thing on this tab with a wrapped sentence in it
    and no way to be shorter except by cutting the words.
    """
    from PySide6.QtWidgets import QGroupBox

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    _at(window, DESKTOP_1080P)
    _ran_a_job(view)
    view.module_report.setPlainText(A_REFUSAL_IN_THE_REPORT_BOX)
    process_events()
    _at(window, (1040, 700))

    card = tab.findChild(QGroupBox)
    assert card is not None, "the custom-module card is gone from the Modules tab"
    assert card.height() >= card.minimumSizeHint().height(), (
        f"the custom-module card is drawn {card.height()}px against the "
        f"{card.minimumSizeHint().height()} it needs at 1040x700"
    )
    assert (
        _drawn_under_their_minimum(tab) == []
    ), f"something on the tab is cut at 1040x700: {_drawn_under_their_minimum(tab)}"
    # And the arithmetic itself, at the seam the defect was in: what the strip
    # says the box needs is what the box's own layout item holds back for it.
    box = tab.layout().itemAt(_index_of(tab, view.module_report))
    assert box is not None
    assert view.module_report_strip.box_minimum() >= box.minimumSize().height(), (
        f"the strip says its box needs {view.module_report_strip.box_minimum()}px and its "
        f"layout item holds back {box.minimumSize().height()}"
    )


def _index_of(tab: Any, widget: Any) -> int:
    """Where `widget` sits in `tab`'s layout."""
    box = tab.layout()
    for index in range(box.count()):
        item = box.itemAt(index)
        if item is not None and item.widget() is widget:
            return index
    raise AssertionError(f"{widget} is not in {tab}'s layout")
