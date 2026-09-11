"""Tests for `ControllerView` (roadmap 4.3) through `ControllerServices` fakes, offscreen."""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

from tests.conftest import pump_until
from yulon import apply as apply_module
from yulon import (
    botlist,
    channel,
    channel_setup,
    commands,
    dashboard,
    docker,
    logsnap,
    networking,
    party,
    purge,
    runner,
    state,
    steam,
    useraccounts,
)
from yulon.apply import Applier, ApplyReport, DockerSql
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
from yulon.manifest import Build, Manifest, ManifestType, Source, parse_manifest
from yulon.manifest_store import ManifestStore
from yulon.networking import NetworkPlan, NetworkReport
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
TBC = load_catalog().get("wow-tbc")
"""8.5b's tree: one bot signal, the account prefix, and no registry table."""


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
        # What the tab handed down as the `values` argument, per call. Recorded
        # because for two of the 41 shipped manifests that argument WAS the
        # defect: the tab called the applier without one at all, and the two
        # modules whose prompts have no default could only fail (2026-09-07).
        self.values: list[object] = []
        self.removed: list[str] = []

    def install(self, manifest: object, values: object = None) -> ApplyReport:  # type: ignore[override]
        item_id = str(manifest.id)  # type: ignore[attr-defined]
        self.installed.append(item_id)
        self.values.append(values)
        return ApplyReport("install", item_id, done=("clone",), rebuild_required=True)

    def remove(self, manifest: object, values: object = None) -> ApplyReport:  # type: ignore[override]
        item_id = str(manifest.id)  # type: ignore[attr-defined]
        self.removed.append(item_id)
        self.values.append(values)
        return ApplyReport("remove", item_id, done=("rm -r",))


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
    assert view.module_list.count() >= 40
    for i in range(view.module_list.count()):
        if view.module_list.item(i).data(256) == "mod-ah-bot":
            view.module_list.setCurrentRow(i)
            break
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
        ApplyReport("install", "mod-solocraft", rebuild_required=True)
    )
    data_only = controller_view_module._format_report(
        ApplyReport("install", "sitmeanrest", restart_recommended=True)
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
        ApplyReport("remove", "mod-solocraft", done=("rm -r",), rebuild_required=True)
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

    The real applier installed `mod-aoe-loot` into `/home/pk/wowserver` and its
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
    for i in range(view.module_list.count()):
        if view.module_list.item(i).data(256) == item_id:
            view.module_list.setCurrentRow(i)
            return
    raise AssertionError(f"{item_id} is not in the Modules list")


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


def test_only_the_two_ah_bot_modules_are_asked_about(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """39 of the 41 must behave exactly as they did — no new dialog at all."""
    asked: list[str] = []

    def asker(parent: object, manifest: object, prompts: object) -> dict[str, str]:
        asked.append(str(manifest.id))  # type: ignore[attr-defined]
        return {p.key: "1" for p in prompts}  # type: ignore[attr-defined]

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0, prompt_asker=asker)
    for i in range(view.module_list.count()):
        if view.module_list.item(i).data(256) not in view._manifests:
            continue
        view.module_list.setCurrentRow(i)
        view._module_action("install")

    assert sorted(asked) == ["mod-ah-bot", "mod-ah-bot-plus"], asked
    applier = view.services.applier
    assert isinstance(applier, _FakeApplier)
    assert len(applier.installed) == view.module_list.count()
    unasked = [v for m, v in zip(applier.installed, applier.values, strict=True) if m not in asked]
    assert all(v is None for v in unasked), "a manifest with no question was given values"


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
# Lane C of `pyplan/phase8-designs/module-from-link-or-folder.md`: the two
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

    def load_all(self, kind: ManifestType) -> Iterator[Manifest]:
        shipped = list(super().load_all(kind))
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

    def install(self, manifest: Manifest, folder: Path | None) -> ApplyReport:
        self.installed.append((manifest.id, folder))
        # Lane A's `complete()` persists inside the install pass, so the row is
        # in the store by the time the report comes back.
        self.store.user[manifest.id] = manifest
        return ApplyReport("install", manifest.id, done=("clone",), rebuild_required=True)

    def forget(self, manifest: Manifest) -> bool:
        self.forgotten.append(manifest.id)
        return self.store.user.pop(manifest.id, None) is not None


def _with_custom_route(
    services: ControllerServices, refusal: str | None = None
) -> _FakeCustomRoute:
    """Put a layered store and the five custom-module seams on `services`."""
    store = _LayeredStore(modules.BUNDLED_MANIFESTS_DIR, modules.GAME)
    route = _FakeCustomRoute(store, refusal=refusal)
    services.store = store
    services.module_from_link = route.derive_link
    services.module_from_folder = route.derive_folder
    services.module_install_custom = route.install
    services.module_forget = route.forget
    return route


def _listed(view: ControllerView) -> list[str]:
    return [view.module_list.item(i).text() for i in range(view.module_list.count())]


def _rows_for(view: ControllerView, item_id: str) -> list[int]:
    """Every row whose manifest id is `item_id` -- the id, not the visible text.

    A shipped manifest's row reads `[module] AoE Loot — ...`: the id is the
    row's `Qt.UserRole` data and appears nowhere in the line, so a search over
    the text finds a custom module (whose name IS its id) and silently misses
    every shipped one.
    """
    return [
        i for i in range(view.module_list.count()) if view.module_list.item(i).data(256) == item_id
    ]


def _row_for(view: ControllerView, item_id: str) -> int:
    rows = _rows_for(view, item_id)
    assert rows, f"{item_id!r} is in no row of the modules list"
    return rows[0]


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
    assert f"[module] mod-my-thing — {CUSTOM_LINK_DESC}" in _listed(view)
    view.module_list.setCurrentRow(_row_for(view, "mod-my-thing"))
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
    assert f"[module] mod-hand-made — {CUSTOM_FOLDER_DESC}" in _listed(view)


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

    view.module_list.setCurrentRow(_row_for(view, "mod-my-thing"))
    view._module_action("remove")

    assert route.forgotten == ["mod-my-thing"]
    assert applier.removed == ["mod-my-thing"]
    assert _rows_for(view, "mod-my-thing") == []

    view.module_list.setCurrentRow(_row_for(view, "mod-aoe-loot"))
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
        ApplyReport("install", "mod-my-thing", done=("clone",), rebuild_required=True)
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
    (`pyplan/gates/bug41-loopback-2026-09-05/yulon-ubuntu-press/ufw-after-apply.txt`),
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

    assert [view._tabs.tabText(i) for i in range(view._tabs.count())].count("Bots") == 0


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

    Every press in `pyplan/gates/8.6-wotlk-yulon-ubuntu2-2026-09-09/` went
    through `gate86b.py`, which is what the exit review calls out
    (`pyplan/phase8-exit-review-2026-09-09.md`, clause 3). This is the same seam
    with a button on it.
    """
    seam = _StubParty()
    view = ControllerView(
        WOTLK, _with_party(ps, tmp_path, seam), status_poll_ms=0, job_runner=run_inline
    )

    assert "Bots" in [view._tabs.tabText(i) for i in range(view._tabs.count())]
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
    (`pyplan/phase8-designs/b-users-surface.md:111`).
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
    ) -> None:
        self.server_dir = server_dir
        self.refusal = refusal
        self.failure = failure
        self.plans = 0
        self.runs: list[bool] = []
        self.busy_seen: list[object] = []
        self._report = report
        self._while_running = while_running

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
    for i in range(view.module_list.count()):
        if view.module_list.item(i).data(256) == "mod-ah-bot":
            view.module_list.setCurrentRow(i)
            break
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

    `is ... Yes` and not `is not ... No`, because Escape and the window's close
    button both answer `NoButton` — and this press writes DDL into a database
    with somebody's characters in it.

    Catches the confirmation skipped, and the verdict read as `is not No`.
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

    `is ... Yes` and not `is not ... No`, because Escape and the window's close
    button both answer `NoButton` — and Yes here is a person saying something
    about their databases that nothing takes back.

    Catches the confirmation skipped, and the verdict read as `is not No`.
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
second source cloned, and a `mangosd.conf` carrying a `SOAP.Enabled` this core
has no key for. `~/tortoise-vm` on `yulon-arch` and the owner's own install on
m910q are both this shape.
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
    assert services.console_probe is not None, (
        "this entry's channel is the console again; a fork install reaches its world the "
        "same way a new one does"
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
