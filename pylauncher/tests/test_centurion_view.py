"""A Centurion (`trinitycore`) server's tabs: the factory, the seams, what each tab draws (T179).

The entry is the Centurion-shaped fixture (`support_trinitycore.centurion_like()`);
the shipped `wow-centurion` entry is T179 Task 7's. Where the view says why a
feature is not offered, the sentence is the family-decision registry's own note
(`catalog/families/decisions.py`), asserted equal here rather than copied.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.support_trinitycore import centurion_like
from yulon import play as play_module
from yulon import purge, runner
from yulon.catalog.families import decisions, mmaps
from yulon.controller_wow_centurion import accounts as centurion_accounts
from yulon.controller_wow_centurion import characters as centurion_characters
from yulon.controller_wow_centurion import console as centurion_console
from yulon.controller_wow_centurion import controller as centurion_controller
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.controller_wow_wotlk.console import ConsoleReply
from yulon.ui import controller_view as view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

ENTRY = centurion_like()


def _quiet_run(
    cmd: list[str], cwd: Path | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """`runner.run` with no Docker behind it: every command answers empty and well."""
    return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "run", _quiet_run)


def _server(tmp_path: Path) -> Path:
    server = tmp_path / "centurion"
    server.mkdir()
    (server / ".db_password").write_text("tc-0123456789abcdef\n", encoding="utf-8")
    return server


def _site(module: str, scope: str) -> decisions.Site:
    return next(s for s in decisions.FAMILY_DECISIONS if (s.module, s.scope) == (module, scope))


# -- the factory -----------------------------------------------------------------------


def test_the_catalog_id_has_a_factory_and_it_builds_the_centurion_package(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path)
    services = ControllerServices.for_entry(ENTRY, server)

    assert "wow-centurion" in view_module._FACTORIES
    assert isinstance(services.controller, centurion_controller.CenturionController)
    assert services.controller.spec == ENTRY.container_spec()
    assert services.controller.server_dir == server
    for seam in (
        "channel_setup",
        "accounts",
        "play",
        "uninstall",
        "rebuild",
        "reset_settings",
        "repair_compose",
        "time_zone",
        "bot_population",
        "pathfinding",
        "database_alone",
    ):
        assert getattr(services, seam) is not None, seam
    # Not offered on this family, each for the reason its registry site gives.
    assert services.my_party is None
    assert services.repair_confs is None
    assert services.bot_dashboard is None
    assert services.store is None and services.applier is None
    assert isinstance(services.uninstall, purge.Uninstaller)


def test_the_command_channel_speaks_soap_on_urn_tc(tmp_path: Path) -> None:
    """TrinityCore's SOAP namespace (`TCSoap.cpp`), not AzerothCore's `urn:AC`."""
    services = ControllerServices.for_entry(ENTRY, _server(tmp_path))
    channel = services.channel_setup
    assert channel is not None
    endpoint = channel._endpoint("YULON_TEST", "pw")  # type: ignore[attr-defined]
    assert endpoint.namespace == "urn:TC"
    assert endpoint.port == 7878


def test_the_console_seam_is_this_entrys_world_and_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def send(command: str, **kwargs: Any) -> ConsoleReply:
        seen.append({"command": command, **kwargs})
        return ConsoleReply(command, ("ok",))

    monkeypatch.setattr(centurion_console._shared, "send_command", send)
    services = ControllerServices.for_entry(ENTRY, _server(tmp_path))
    services.send_console("server info")

    (call,) = seen
    assert call["command"] == "server info"
    assert call["container"] == ENTRY.container_spec().world
    assert call["prompt"] == "TC>"
    assert call["prompt_precedes_answer"] is True


def test_the_account_seam_writes_trinitycores_scheme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def create(sql: object, name: str, password: str, **kwargs: Any) -> object:
        seen.append({"sql": sql, "name": name, **kwargs})
        return "made"

    monkeypatch.setattr(centurion_accounts.writer, "create_account", create)
    services = ControllerServices.for_entry(ENTRY, _server(tmp_path))
    assert services.create_account("bob", "hunter22", 3) == "made"

    (call,) = seen
    assert call["scheme"] == "trinitycore"
    assert call["max_gm_level"] == 3
    assert call["gm_level"] == 3
    sql = call["sql"]
    assert sql.db_container == ENTRY.container_spec().db
    assert sql.schemas["auth"] == ENTRY.databases.auth


def test_the_backup_seam_names_this_entrys_containers_and_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def backup(server_dir: Path, mysql: object, **kwargs: Any) -> object:
        seen.append({"server_dir": server_dir, "mysql": mysql, **kwargs})
        return "dumped"

    monkeypatch.setattr(wotlk_maintenance, "backup", backup)
    server = _server(tmp_path)
    services = ControllerServices.for_entry(ENTRY, server)
    assert services.backup() == "dumped"

    (call,) = seen
    assert call["spec"] == ENTRY.container_spec()
    assert call["core_databases"] == ENTRY.core_databases()
    assert call["mysql"].client == "mysql"
    assert call["mysql"].db_container == ENTRY.container_spec().db


def test_the_dump_keeps_routines_triggers_and_events() -> None:
    argv = centurion_maintenance_argv()
    for flag in ("--routines", "--triggers", "--events"):
        assert flag in argv, argv


def centurion_maintenance_argv() -> list[str]:
    from yulon.controller_wow_centurion import maintenance

    return maintenance.mysql_for(ENTRY, "pw")._dump_argv(ENTRY.databases.world)


def test_no_verb_is_drawn_before_task_9_watched_it_work(tmp_path: Path) -> None:
    services = ControllerServices.for_entry(ENTRY, _server(tmp_path))
    assert centurion_characters.CONFIRMED_LIVE == frozenset()
    assert set(services.characters_withheld) == set(play_module.VERBS)
    assert isinstance(services.play, play_module.InstallPlay)
    assert services.play.withheld == services.characters_withheld


def test_a_withheld_verb_is_refused_before_anything_is_asked() -> None:
    class _NoSql:
        def query(self, db: str, statement: str) -> str:
            raise AssertionError("a withheld verb read the database")

    made = play_module.InstallPlay(
        ENTRY,
        Path("x"),
        sql=_NoSql(),
        channel_for_saved=lambda: (_ for _ in ()).throw(AssertionError("asked the channel")),
        withheld={"revive": "not offered here yet"},
    )
    outcome = made.revive("Guglu")
    assert not outcome.done
    assert outcome.problem == "revive is not offered on this server: not offered here yet."
    with pytest.raises(ValueError, match="no such Characters verb"):
        play_module.InstallPlay(
            ENTRY, Path("x"), sql=_NoSql(), channel_for_saved=lambda: None, withheld={"x": "y"}
        )


def test_the_modules_note_is_said_in_place_of_an_empty_list(tmp_path: Path) -> None:
    services = ControllerServices.for_entry(ENTRY, _server(tmp_path))
    assert services.no_modules_note.startswith("Centurion has no add-on modules")


# -- what the tabs draw ------------------------------------------------------------------


class _Pathfinding:
    def __init__(self, status: mmaps.MmapsStatus) -> None:
        self.now = status
        self.started = 0
        self.stopped = 0

    def status(self) -> mmaps.MmapsStatus:
        return self.now

    def start(self) -> str:
        self.started += 1
        self.now = mmaps.MmapsStatus(state="running", percent=0)
        return "Pathfinding data is being made."

    def stop(self) -> str:
        self.stopped += 1
        self.now = mmaps.MmapsStatus(state="not-started")
        return "Stopped."


def _view(tmp_path: Path, **seams: Any) -> ControllerView:
    services = replace(ControllerServices.for_entry(ENTRY, _server(tmp_path)), **seams)
    return ControllerView(ENTRY, services, status_poll_ms=0, job_runner=run_inline)


def _seam(fake: _Pathfinding) -> view_module.Pathfinding:
    return view_module.Pathfinding(status=fake.status, start=fake.start, stop=fake.stop)


def test_the_server_tab_says_how_far_the_pathfinding_data_has_got(
    qapp: object, tmp_path: Path
) -> None:
    fake = _Pathfinding(mmaps.MmapsStatus(state="running", percent=37))
    view = _view(tmp_path, pathfinding=_seam(fake))

    view.refresh_pathfinding()

    assert view.pathfinding_label.text() == fake.now.line()
    assert "37 %" in view.pathfinding_label.text()
    assert "already runs without it" in view.pathfinding_label.text()
    assert not view.pathfinding_label.isHidden()
    assert view.pathfinding_start_button.isHidden()
    assert not view.pathfinding_stop_button.isHidden()

    view.stop_pathfinding()

    assert fake.stopped == 1
    assert view.pathfinding_label.text() == fake.now.line()
    assert not view.pathfinding_start_button.isHidden()
    assert view.pathfinding_stop_button.isHidden()


def test_a_finished_set_asks_for_a_restart_and_offers_nothing(qapp: object, tmp_path: Path) -> None:
    fake = _Pathfinding(
        mmaps.MmapsStatus(state="done", percent=100, pathfinding_on=True, restart_needed=True)
    )
    view = _view(tmp_path, pathfinding=_seam(fake))
    view.refresh_pathfinding()
    assert "Restart the server to use it" in view.pathfinding_label.text()
    assert view.pathfinding_start_button.isHidden()
    assert view.pathfinding_stop_button.isHidden()


def test_start_is_offered_after_a_failure_and_never_while_a_rebuild_runs(
    qapp: object, tmp_path: Path
) -> None:
    fake = _Pathfinding(mmaps.MmapsStatus(state="failed", error="the generator crashed."))
    view = _view(tmp_path, pathfinding=_seam(fake))
    view.refresh_pathfinding()
    assert "could not be made" in view.pathfinding_label.text()
    assert not view.pathfinding_start_button.isHidden()
    assert view.pathfinding_start_button.isEnabled()

    view._set_busy(True)  # what a Rebuild, an Update, a Return or an Uninstall press does
    assert not view.pathfinding_start_button.isEnabled()
    view.start_pathfinding()
    assert fake.started == 0, "Start ran beside a rebuild"

    view._set_busy(False)
    assert view.pathfinding_start_button.isEnabled()
    view.start_pathfinding()
    assert fake.started == 1
    assert "0 %" in view.pathfinding_label.text()


def test_a_server_without_the_job_draws_no_pathfinding_line(qapp: object, tmp_path: Path) -> None:
    view = _view(tmp_path, pathfinding=None)
    view.refresh_pathfinding()
    assert view.pathfinding_label.isHidden()
    assert view.pathfinding_start_button.isHidden()
    assert view.pathfinding_stop_button.isHidden()


def test_my_party_says_the_registrys_own_reason(qapp: object, tmp_path: Path) -> None:
    """The UI text EQUALS the not-available note (T179 Task 1's carried item)."""
    view = _view(tmp_path)
    decision = _site("yulon.party", "InstallParty.for_entry_is_possible").decisions["trinitycore"]
    assert decision.kind == "not-available"
    assert view.party_panel is None
    assert view.my_party_absent.text() == decision.note


def test_every_trinitycore_reason_a_tab_says_is_the_registrys_note(
    qapp: object, tmp_path: Path
) -> None:
    """Each not-available decision for this family that a tab draws, drawn word for word."""
    view = _view(tmp_path)
    drawn = {
        ("yulon.party", "InstallParty.for_entry_is_possible"): view.my_party_absent.text(),
    }
    withheld = [
        (site.module, site.scope)
        for site in decisions.FAMILY_DECISIONS
        if site.decisions["trinitycore"].kind == "not-available"
    ]
    for key in withheld:
        note = _site(*key).decisions["trinitycore"].note
        if key in drawn:
            assert drawn[key] == note, key
        else:
            # Not drawn at all: the control is absent on every game whose module
            # lacks it, so there is no place where a sentence would stand.
            assert key == ("yulon.catalog.bot_dashboard", "conf_file"), key
            assert view.services.bot_dashboard is None


def test_the_characters_tab_draws_no_unconfirmed_verb_and_says_why(
    qapp: object, tmp_path: Path
) -> None:
    view = _view(tmp_path)
    assert view.character_buttons() == ()
    said = view.characters_withheld_label.text()
    assert "Teleport" in said and "Send everything worn by" in said
    assert "checked against a live Centurion server" in said
    assert not view.characters_withheld_label.isHidden()


def test_a_confirmed_verb_is_drawn_and_the_rest_stay_out(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(centurion_characters, "CONFIRMED_LIVE", frozenset({"teleport", "revive"}))
    view = _view(tmp_path)
    assert view.character_buttons() == (view.teleport_button, view.revive_button)
    said = view.characters_withheld_label.text()
    assert "Teleport" not in said and "Revive" not in said
    assert "Set level" in said


def test_the_modules_tab_says_centurion_has_no_add_on_modules(qapp: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    view.reload_modules()
    assert view.modules_panel.empty_label.text() == view.services.no_modules_note
    assert view.modules_panel.empty_label.text().startswith("Centurion has no add-on modules")
