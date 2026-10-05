"""A Centurion (`trinitycore`) server's tabs: the factory, the seams, what each tab draws (T179).

The entry is the Centurion-shaped fixture (`support_trinitycore.centurion_like()`);
the shipped `wow-centurion` entry is T179 Task 7's. Where the view says why a
feature is not offered, the sentence is the family-decision registry's own note
(`catalog/families/decisions.py`), asserted equal here rather than copied.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import process_events, wait_for_panel
from tests.support_trinitycore import centurion_like
from yulon import docker, purge, runner
from yulon import play as play_module
from yulon.catalog.families import decisions, mmaps, trinitycore
from yulon.controller_wow_centurion import accounts as centurion_accounts
from yulon.controller_wow_centurion import characters as centurion_characters
from yulon.controller_wow_centurion import console as centurion_console
from yulon.controller_wow_centurion import controller as centurion_controller
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.controller_wow_wotlk.console import ConsoleReply
from yulon.ui import controller_view as view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline
from yulon.ui.widgets.tuning_panel import picker_label

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
        "world_upkeep",
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


T208_WORKED = frozenset({"set_level", "teleport", "mail_gold", "send_gear", "rename"})
"""The verbs T208's live check (yulon-win11, 2026-10-04) pressed and watched work."""


def test_the_verbs_t208_watched_work_are_confirmed_and_revive_is_not(tmp_path: Path) -> None:
    services = ControllerServices.for_entry(ENTRY, _server(tmp_path))
    assert centurion_characters.CONFIRMED_LIVE == T208_WORKED
    assert T208_WORKED < set(play_module.VERBS), "a name that is not a play.VERBS key"
    assert dict(services.characters_withheld) == {"revive": centurion_characters.REVIVE_CRASHED}
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


# -- what an update left owing: the map data, the world tables (Task 6, fix round 1) ----------


class _Upkeep:
    """The two sentences and the two presses; each press pays its own sentence."""

    def __init__(self, map_data: str | None = None, world: str | None = None) -> None:
        self.map_data, self.world = map_data, world
        self.reextracts: list[dict[str, object]] = []
        self.finishes: list[object] = []

    def read(self) -> tuple[str | None, str | None]:
        return self.map_data, self.world

    def reextract(self, cancel: object = None, client_dir: Path | None = None) -> Iterator[str]:
        self.reextracts.append({"cancel": cancel, "client_dir": client_dir})
        yield "--- client-data"
        self.map_data = None

    def finish(self, cancel: object = None) -> Iterator[str]:
        self.finishes.append(cancel)
        yield "Importing 1 world tables again"
        self.world = None


def _upkeep(
    fake: _Upkeep, *, reextract: bool = True, finish: bool = True
) -> view_module.WorldUpkeep:
    return view_module.WorldUpkeep(
        read=fake.read,
        reextract=fake.reextract if reextract else None,
        finish_world=fake.finish if finish else None,
    )


MAP_SENTENCE = "Centurion's map data must be extracted again: ..."
WORLD_SENTENCE = "Centurion's last update did not finish importing its world tables: ..."


def test_the_server_tab_says_the_map_data_must_be_extracted_and_the_press_streams_it(
    qapp: object, tmp_path: Path
) -> None:
    fake = _Upkeep(map_data=MAP_SENTENCE)
    client = tmp_path / "World of Warcraft 3.3.5a"
    view = _view(tmp_path, world_upkeep=_upkeep(fake), client_dir=client)

    view.refresh_world_upkeep()

    assert view.world_upkeep_label.text() == MAP_SENTENCE
    assert not view.world_upkeep_label.isHidden()
    assert not view.reextract_button.isHidden() and view.reextract_button.isEnabled()
    assert view.reextract_button.text() == "Re-extract map data"
    assert view.finish_world_button.isHidden()

    assert view.reextract_map_data() is True
    wait_for_panel(view.rebuild_log)
    process_events()
    (call,) = fake.reextracts
    assert call["client_dir"] == client, "the tab's own client folder"
    assert isinstance(call["cancel"], docker.CancelWithForce)
    assert view.world_upkeep_label.isHidden(), "read again once the press ended"
    assert view.reextract_button.isHidden()


def test_finish_the_world_update_is_offered_and_streams_into_the_panel(
    qapp: object, tmp_path: Path
) -> None:
    fake = _Upkeep(world=WORLD_SENTENCE)
    view = _view(tmp_path, world_upkeep=_upkeep(fake))
    view.refresh_world_upkeep()
    assert view.world_upkeep_label.text() == WORLD_SENTENCE
    assert view.reextract_button.isHidden()
    assert not view.finish_world_button.isHidden() and view.finish_world_button.isEnabled()
    assert view.finish_world_button.text() == "Finish the world update"

    assert view.finish_world_update() is True
    wait_for_panel(view.rebuild_log)
    process_events()
    assert len(fake.finishes) == 1
    assert view.world_upkeep_label.isHidden()


def test_both_presses_are_held_while_another_press_runs(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[str] = []
    monkeypatch.setattr(
        view_module.QMessageBox, "information", lambda _parent, _title, text: told.append(text)
    )
    fake = _Upkeep(map_data=MAP_SENTENCE, world=WORLD_SENTENCE)
    view = _view(tmp_path, world_upkeep=_upkeep(fake))
    view.refresh_world_upkeep()
    assert view.reextract_button.isEnabled() and view.finish_world_button.isEnabled()

    view._set_busy(True)  # what a Rebuild, an Update, a Start or an Uninstall press does
    assert not view.reextract_button.isEnabled()
    assert not view.finish_world_button.isEnabled()
    assert view.reextract_map_data() is False
    assert view.finish_world_update() is False
    assert fake.reextracts == [] and fake.finishes == []
    assert len(told) == 2 and "Nothing was started" in told[0]

    view._set_busy(False)
    assert view.reextract_button.isEnabled() and view.finish_world_button.isEnabled()


def test_a_tab_with_no_press_for_a_sentence_draws_the_sentence_alone(
    qapp: object, tmp_path: Path
) -> None:
    """Inside a WSL distro the map data cannot be extracted from here: no button to name."""
    fake = _Upkeep(map_data=MAP_SENTENCE, world=WORLD_SENTENCE)
    view = _view(tmp_path, world_upkeep=_upkeep(fake, reextract=False, finish=False))
    view.refresh_world_upkeep()
    assert MAP_SENTENCE in view.world_upkeep_label.text()
    assert WORLD_SENTENCE in view.world_upkeep_label.text()
    assert view.reextract_button.isHidden() and view.finish_world_button.isHidden()


def test_inside_a_wsl_distro_the_map_data_sentence_names_no_press_on_this_tab(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path)
    (server / trinitycore.REEXTRACT_FILE).write_text(
        '{"version": 1, "changed": ["src/centurion/centurion/dbc/Spell.dbc"]}', encoding="utf-8"
    )
    seam = view_module._world_upkeep(ENTRY, server, wsl_distro="Ubuntu")
    assert seam is not None and seam.reextract is None
    map_data, world = seam.read()
    assert map_data is not None and "inside a WSL distro" in map_data
    assert "Stop the server, then press" not in map_data
    assert world is None
    here = view_module._world_upkeep(ENTRY, server, wsl_distro=None)
    assert here is not None and here.reextract is not None
    said, _ = here.read()
    assert said is not None and "Stop the server, then press" in said


def test_a_server_with_nothing_owed_draws_no_upkeep_line(qapp: object, tmp_path: Path) -> None:
    view = _view(tmp_path, world_upkeep=_upkeep(_Upkeep()))
    view.refresh_world_upkeep()
    assert view.world_upkeep_label.isHidden()
    assert view.reextract_button.isHidden() and view.finish_world_button.isHidden()
    (tmp_path / "none").mkdir()
    view = _view(tmp_path / "none", world_upkeep=None)
    view.refresh_world_upkeep()
    assert view.world_upkeep_label.isHidden()


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


def test_the_characters_tab_draws_the_five_confirmed_verbs_and_not_revive(
    qapp: object, tmp_path: Path
) -> None:
    view = _view(tmp_path)
    assert view.character_buttons() == (
        view.teleport_button,
        view.set_level_button,
        view.rename_button,
        view.mail_gold_button,
        view.send_gear_button,
    )
    assert view.revive_button not in view.character_buttons()
    assert not view.revive_button.isVisibleTo(view)


def test_revives_line_says_it_crashed_the_server_and_names_nothing_else(
    qapp: object, tmp_path: Path
) -> None:
    view = _view(tmp_path)
    said = view.characters_withheld_label.text()
    assert not view.characters_withheld_label.isHidden()
    assert said == (
        "Not offered on this server yet: Revive — it crashed the world server when it was "
        "tried, so it stays off until that is fixed."
    )
    assert "checked against a live" not in said, "Revive was checked: it is the crash that holds it"
    for drawn in ("Teleport", "Set level", "Rename", "Send gold", "Send everything"):
        assert drawn not in said, drawn


def test_the_shipped_centurion_entry_withholds_only_revive(tmp_path: Path) -> None:
    from yulon.catalog.catalog import load_catalog

    entry = load_catalog().get("wow-centurion")
    services = ControllerServices.for_entry(entry, _server(tmp_path))
    assert set(services.characters_withheld) == {"revive"}


def test_the_other_games_characters_tabs_withhold_nothing(qapp: object, tmp_path: Path) -> None:
    from yulon.catalog.catalog import load_catalog

    others = [entry for entry in load_catalog().games if entry.id != "wow-centurion"]
    assert {entry.id for entry in others} >= {"wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise"}
    for entry in others:
        services = ControllerServices.for_entry(entry, tmp_path / entry.id)
        assert dict(services.characters_withheld) == {}, entry.id
        view = ControllerView(entry, services, status_poll_ms=0, job_runner=run_inline)
        assert view.characters_withheld_label.text() == "", entry.id
        assert view.characters_withheld_label.isHidden(), entry.id
        if entry.play is not None:
            assert view.revive_button in view.character_buttons(), entry.id
            assert view.teleport_button in view.character_buttons(), entry.id


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


# -- fix round 1 ------------------------------------------------------------------------


def test_the_raw_editor_lists_centurions_own_confs_read_only(qapp: object, tmp_path: Path) -> None:
    """Spec §2: worldserver.conf (and authserver.conf) on Tuning, shown as AzerothCore's are."""
    view = _view(tmp_path)
    server = view.services.controller.server_dir
    etc = server / "etc"
    etc.mkdir(exist_ok=True)
    for name in ("worldserver.conf", "authserver.conf", "playerbots.conf"):
        (etc / name).write_text("Key = 1\n", encoding="utf-8")

    files = view._tuning_files()
    assert "etc/worldserver.conf" in files and "etc/authserver.conf" in files
    assert view._tuning_core_files() == ("etc/worldserver.conf", "etc/authserver.conf")

    view.reload_tuning()
    labels = {button.toolTip(): button.text() for button in view.tuning_panel.file_buttons()}
    assert labels["etc/worldserver.conf"] == picker_label(
        "etc/worldserver.conf", duplicate=False, read_only=True
    )
    view.tuning_panel._file_picked("etc/worldserver.conf")
    assert view.tuning_panel.current_file() == "etc/worldserver.conf"
    assert view.tuning_panel.editor.isReadOnly()
    assert not view.tuning_panel.file_save_button.isEnabled()
    view.save_tuning_file("Key = 2\n")
    assert (etc / "worldserver.conf").read_text(encoding="utf-8") == "Key = 1\n"


def test_the_other_games_raw_editor_lists_what_it_always_did(qapp: object, tmp_path: Path) -> None:
    from yulon.catalog.catalog import load_catalog

    for game in ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise"):
        entry = load_catalog().get(game)
        assert view_module.reset_defaults.read_only_confs(entry) == (
            view_module.TUNING_CORE_FILES
        ), game


def test_my_party_on_centurion_is_the_registrys_note_and_the_shipped_games_keep_theirs(
    tmp_path: Path,
) -> None:
    from yulon.catalog.catalog import load_catalog

    assert view_module._no_my_party(ENTRY) == decisions.PARTY_REASON
    for game in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        entry = load_catalog().get(game)
        said = view_module._no_my_party(entry)
        assert said.startswith("Building a bot party from the launcher works on WoW WotLK only")
        assert f"so {entry.name} has no party control here" in said


def test_the_withheld_line_uses_a_dash() -> None:
    said = view_module._withheld_sentence({"revive": "why"})
    assert said == "Not offered on this server yet: Revive — why."


class _Deferred:
    """A job runner that holds every job until the test runs it, in the order it chooses."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Any, Any, Any]] = []

    def __call__(self, work: Any, on_done: Any, on_error: Any) -> None:
        self.jobs.append((work, on_done, on_error))

    def run(self, index: int) -> None:
        work, on_done, on_error = self.jobs.pop(index)
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - delivered as the runner would
            on_error(exc)
            return
        on_done(result)


def test_a_reading_that_lands_after_a_press_and_its_own_reading_is_dropped(
    qapp: object, tmp_path: Path
) -> None:
    """M2: the read out before Stop must not draw over the read made after it."""
    fake = _Pathfinding(mmaps.MmapsStatus(state="running", percent=37))
    jobs = _Deferred()
    services = replace(
        ControllerServices.for_entry(ENTRY, _server(tmp_path)), pathfinding=_seam(fake)
    )
    view = ControllerView(ENTRY, services, status_poll_ms=0, job_runner=jobs)
    jobs.jobs.clear()  # whatever the tab queued while it was built

    view.refresh_pathfinding()  # read 1 goes out...
    stale_work, stale_done, _ = jobs.jobs.pop(0)
    stale_answer = stale_work()  # ...and Docker answers "running 37 %" now
    view.stop_pathfinding()  # the press
    jobs.run(0)  # the press runs: stopped
    assert len(jobs.jobs) == 1, "the press did not ask again"
    jobs.run(0)  # read 2: "not started"
    assert "has not been made yet" in view.pathfinding_label.text()

    stale_done(stale_answer)  # read 1's answer lands last
    assert "has not been made yet" in view.pathfinding_label.text(), "an older read drew"
    assert not view.pathfinding_start_button.isHidden()
