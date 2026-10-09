"""T622 item 1: the last feature writes to a server take the cross-process hold too.

T610 put the Accounts, Characters, Tuning and channel writes under `docker.server_hold`. Left:
the Networking tab's Apply (the realmlist SQL and the applied mode's file), the Tuning tab's
time-zone row, the Bots tab's bot count, the Tuning tab's Reset to defaults and its Undo, and
the uninstall's removal of volumes, images and the folder. Each now runs inside the hold, and
while another Yu'lon holds the server it says that Yu'lon's sentence and writes nothing. A
guard classifies every `ControllerServices` seam, so a new one has to say whether it writes.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from tests.test_controller_view import _Ps
from tests.test_more_writes_hold import CATALOG_WOTLK, HELD, _Hold, _Nothing
from yulon import bot_population as botpop
from yulon import docker, networking, platform, purge, reset_defaults, server_time_zone
from yulon.catalog.catalog import load_catalog
from yulon.ui import controller_view as controller_view_module


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    from yulon import runner
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text("yulon.local/wotlk-server:native\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


def _assembled(server: Path) -> controller_view_module.ControllerServices:
    return controller_view_module._assemble(
        CATALOG_WOTLK,
        server,
        client_dir=None,
        wsl_distro=None,
        controller=_Nothing(),  # type: ignore[arg-type]
        sql=_Nothing(),  # type: ignore[arg-type]
        send_console=lambda _c: None,  # type: ignore[arg-type,return-value]
        create_account=lambda name, pw, level: None,  # type: ignore[arg-type,return-value]
        store=None,
        applier=None,
        backup=lambda: None,  # type: ignore[arg-type,return-value]
        plan_restore=lambda *_a: None,  # type: ignore[arg-type,return-value]
        restore=lambda _p: None,  # type: ignore[arg-type,return-value]
    )


def _held_by_another_yulon(fake_docker: Path, server: Path) -> Any:
    from tests.test_server_reservation import _another_yulon_holds

    return _another_yulon_holds(
        fake_docker, docker.SERVER_CLAIM_PREFIX + str(docker.folder_id(server))
    )


@pytest.fixture
def server(tmp_path: Path) -> Path:
    path = tmp_path / "server"
    path.mkdir()
    return path


# ------------------------------------------------------------------ the shared assembly's routes


def test_networking_apply_while_another_yulon_holds_the_server_applies_nothing(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: pass `networking.apply` through unwrapped in `_assemble`."""
    from tests.test_server_reservation import HOLDER_PRESS

    applied: list[object] = []
    monkeypatch.setattr(networking, "apply", lambda plan, **_kw: applied.append(plan))
    services = _assembled(server)
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            services.network_apply(object())  # type: ignore[arg-type]
        assert HOLDER_PRESS in str(refused.value)
        assert "Apply the network plan" in str(refused.value)
    finally:
        theirs.kill()
    assert applied == []


def test_networking_apply_runs_when_nobody_holds_the_server(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    applied: list[object] = []
    monkeypatch.setattr(networking, "apply", lambda plan, **_kw: applied.append(plan) or "report")
    plan = object()
    assert _assembled(server).network_apply(plan) == "report"  # type: ignore[arg-type]
    assert applied == [plan]


def test_the_time_zone_write_while_another_yulon_holds_the_server_writes_nothing(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: build the route in `_assemble` without `hold_server=`."""
    written: list[str] = []
    monkeypatch.setattr(server_time_zone, "write", lambda *_a: written.append("zone"))
    route = _assembled(server).time_zone
    assert route is not None
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        with pytest.raises(docker.ServerReserved, match="Set the server's time zone"):
            route.write("UTC")
    finally:
        theirs.kill()
    assert written == []


def test_the_time_zone_write_runs_when_nobody_holds_the_server(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[str] = []
    monkeypatch.setattr(
        server_time_zone, "write", lambda _e, _d, zone: written.append(zone) or "done"
    )
    route = _assembled(server).time_zone
    assert route is not None
    assert route.write("UTC") == "done"
    assert written == ["UTC"]


def test_the_time_zone_read_takes_no_hold(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read must go on while another Yu'lon works: the tab shows the file as it is."""
    monkeypatch.setattr(server_time_zone, "read", lambda _e, _d: "reading")
    route = _assembled(server).time_zone
    assert route is not None
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        assert route.read() == "reading"
    finally:
        theirs.kill()


def test_the_bot_count_write_while_another_yulon_holds_the_server_writes_nothing(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[int] = []
    monkeypatch.setattr(botpop, "write", lambda _e, _d, n: written.append(n))
    route = _assembled(server).bot_population
    if route is None:
        pytest.skip("this game has no bot count")
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        with pytest.raises(docker.ServerReserved, match="Set the number of random bots"):
            route.write(100)
    finally:
        theirs.kill()
    assert written == []


def test_reset_to_default_while_another_yulon_holds_the_server_writes_nothing(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset: list[object] = []
    monkeypatch.setattr(reset_defaults, "reset", lambda *_a, **_kw: reset.append(1))
    route = _assembled(server).reset_settings
    assert route is not None
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        with pytest.raises(docker.ServerReserved, match="Put settings back to how they were"):
            route(("a.conf",), {}, None)
    finally:
        theirs.kill()
    assert reset == []


# ------------------------------------------------------------------ what the tabs say


def _held_sentence_raiser(*_a: Any, **_k: Any) -> Any:
    raise docker.ServerReserved(HELD, docker.ServerHolder("yulon-busy-x", "id"))


def test_the_networking_tab_says_the_holders_sentence_when_apply_is_refused(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_controller_view import _netsh_plan, _services
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _p: run_inline)
    services = _services(ps, tmp_path, [])
    services.network_plan = _netsh_plan
    services.network_apply = _held_sentence_raiser
    view = controller_view_module.ControllerView(CATALOG_WOTLK, services, status_poll_ms=0)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.show_network_plan()
    view.apply_network_plan()
    shown = view.network_text.toPlainText()
    assert shown.endswith("\n" + HELD), shown
    assert "did not finish" not in shown
    assert failures == [HELD]
    assert view.apply_button.isEnabled()


def test_the_time_zone_row_says_the_holders_sentence_and_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_controller_view import _reset_yes, _services, _wotlk_override
    from tests.test_time_zone_view import OSLO, OVERRIDE, _pick
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _p: run_inline)
    installed = _wotlk_override(tmp_path)
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services,
        "time_zone",
        server_time_zone.time_zone_route(CATALOG_WOTLK, tmp_path, hold_server=_Hold(refuse=True)),
    )
    view = controller_view_module.ControllerView(CATALOG_WOTLK, services, status_poll_ms=0)
    _reset_yes(monkeypatch)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    _pick(view, OSLO)
    view.time_zone_apply_button.click()
    assert view.tuning_report.toPlainText() == HELD
    assert failures == [HELD]
    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8") == installed
    assert not list(tmp_path.glob("*.bak"))


def test_the_bot_count_box_says_the_holders_sentence_and_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_controller_view import BOT_CONF, TBC, _bot_conf, _reset_yes, _services
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _p: run_inline)
    before = _bot_conf(tmp_path)
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services,
        "bot_population",
        botpop.bot_count_route(TBC, tmp_path, hold_server=_Hold(refuse=True)),
    )
    view = controller_view_module.ControllerView(TBC, services, status_poll_ms=0)
    _reset_yes(monkeypatch)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.bot_count_box.setValue(50)
    view.bot_count_apply_button.click()
    assert view.bot_count_report.text() == HELD
    assert failures == [HELD]
    assert (tmp_path / BOT_CONF).read_bytes() == before
    assert not list(tmp_path.rglob("*.bak"))


def test_reset_to_default_says_the_holders_sentence_and_keeps_the_undo(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_controller_view import (
        TUNING_RESET_ALL,
        _menu_action,
        _reset_view,
        _reset_yes,
        _wotlk_server,
    )
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _p: run_inline)
    _wotlk_server(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = _reset_view(ps, tmp_path, route=_held_sentence_raiser)
    kept = ("an earlier press's record",)
    view._last_reset = kept  # type: ignore[assignment]
    _reset_yes(monkeypatch)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    _menu_action(view, TUNING_RESET_ALL).trigger()
    assert view.tuning_report.toPlainText() == HELD
    assert failures == [HELD]
    assert view._last_reset == kept, "a refusal wrote nothing, so the last record stands"
    assert view._busy is False
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_undo_of_a_reset_while_another_yulon_holds_the_server_puts_nothing_back(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_controller_view import (
        TUNING_RESET_ALL,
        TUNING_RESET_UNDO,
        _menu_action,
        _reset_view,
        _reset_yes,
        _wotlk_server,
    )
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _p: run_inline)
    defaults = _wotlk_server(tmp_path)
    view = _reset_view(ps, tmp_path)
    _reset_yes(monkeypatch)
    _menu_action(view, TUNING_RESET_ALL).trigger()
    after_reset = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert all((tmp_path / f).read_bytes() == defaults[f] for f in defaults), "control: reset ran"
    object.__setattr__(view.services, "hold_server", _Hold(refuse=True))
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    _menu_action(view, TUNING_RESET_UNDO).trigger()
    assert view.tuning_report.toPlainText() == HELD
    assert failures == [HELD]
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == after_reset
    assert view.tuning_reset_undo_action.isEnabled(), "the undo is still on offer"
    object.__setattr__(view.services, "hold_server", _Hold())
    _menu_action(view, TUNING_RESET_UNDO).trigger()
    assert all((tmp_path / f).read_bytes() == b"Key = 2\n" for f in defaults), "control: undo ran"


# ------------------------------------------------------------------ the uninstall


def test_an_uninstall_while_another_yulon_holds_the_server_removes_nothing(
    tmp_path: Path,
) -> None:
    """Mutation: take no hold in `Uninstaller.run`, or ignore the refusal."""
    from tests.test_purge import _recorder

    rec = _recorder(tmp_path)
    installed = rec.uninstaller(hold_server=_Hold(refuse=True))
    with pytest.raises(purge.PurgeRefusal, match="Another Yu'lon is working on WoW"):
        installed.run(keep_characters=False)
    removing = ("snapshot", "remove_", "forget")
    assert [step for step in rec.order if step.startswith(removing)] == []


def test_an_uninstall_removes_everything_inside_the_hold(tmp_path: Path) -> None:
    """The containers, volumes, images, the folder and the record: all while the hold is held."""
    from tests.test_purge import _recorder

    rec = _recorder(tmp_path)
    hold = _Hold(rec.order)
    rec.uninstaller(hold_server=hold).run(keep_characters=False)
    taken = rec.order.index(f"hold:{purge.UNINSTALL_PRESS}")
    released = rec.order.index("release")
    for step in ("snapshot", "remove_containers", f"remove_folder:{rec.server_dir}", "forget"):
        assert taken < rec.order.index(step) < released, (step, rec.order)
    assert rec.order.count("release") == 1


def test_an_uninstall_run_by_the_real_hold_removes_the_folder_and_leaves_none_behind(
    fake_docker: Path, tmp_path: Path
) -> None:
    """The release must not write into, and so bring back, the folder the uninstall removed."""
    from tests.test_purge import _recorder

    rec = _recorder(tmp_path)
    (rec.server_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    hold = _server_hold(rec.server_dir)
    report = rec.uninstaller(
        hold_server=hold, remove_folder=lambda path: __import__("shutil").rmtree(path)
    ).run(keep_characters=False)
    assert report.folder_removed
    assert not rec.server_dir.exists()


def _server_hold(server: Path) -> Any:
    from yulon.ui.controller_view import _server_hold_for

    return _server_hold_for(CATALOG_WOTLK, server, CATALOG_WOTLK.container_spec(), wsl_distro=None)


def _constructions_of(callee: str) -> list[tuple[str, int, bool]]:
    from tests.test_more_writes_hold import _constructions

    return _constructions(callee)


def test_every_uninstaller_in_yulon_is_built_with_the_hold() -> None:
    """Mutation: build one of the three `purge.Uninstaller(`s without `hold_server=`."""
    calls = _constructions_of("Uninstaller")
    assert len(calls) >= 3, "the guard is blind"
    assert [f"{n}:{line}" for n, line, held in calls if not held] == []


# ------------------------------------------------------------------ every seam says what it does

_HERE = "tests.test_last_writes_hold"
_MORE = "tests.test_more_writes_hold"
_SEAMS = "tests.test_server_hold_seams"
_MODULES = "tests.test_module_actions_hold"

# Every field of `ControllerServices`, and what it is to the server's databases and files. A new
# field fails `test_every_services_seam_says_what_it_writes` until it is put in one of:
#   held:    writes, inside the hold; the value names the test that drives it with a refusing hold
#   lower:   writes, and the layer below takes the hold (the engine's presses, the Applier)
#   reads:   reads the server, or the world, and writes none of it
#   outside: writes something that is not this server's databases or files
#   open:    writes and does not hold yet; the value names the ticket (and the set is pinned below)
SEAMS: dict[str, tuple[str, str]] = {
    "controller": ("lower", "Start/Stop/Restart are engine presses (controller.py reserves)"),
    "logs_source": ("reads", "follows a container's log"),
    "send_console": ("outside", "a live command to the running world, not its files"),
    "store": ("reads", "the manifest store"),
    "applier": (
        "held",
        f"{_MODULES}.test_an_install_refused_by_the_hold_clones_copies_and_makes_nothing",
    ),
    "network_plan": ("reads", "plans; writes nothing"),
    "network_apply": (
        "held",
        f"{_HERE}.test_networking_apply_while_another_yulon_holds_the_server_applies_nothing",
    ),
    "create_account": (
        "held",
        f"{_MORE}.test_the_account_create_seam_is_made_under_the_hold_by_the_shared_assembly",
    ),
    "backup": ("open", "T604: the Maintenance tab's Backups box"),
    "backups_dir": ("reads", "a path"),
    "plan_restore": ("reads", "plans; writes nothing"),
    "restore": ("open", "T604: the Maintenance tab's Backups box (it holds its own, apart)"),
    "interrupted_restore": ("reads", "reads a record"),
    "forget_interrupted": ("open", "T604: the Maintenance tab's Backups box"),
    "hold_server": (
        "held",
        f"{_MORE}.test_a_raw_conf_save_while_another_yulon_holds_the_server_writes_nothing",
    ),
    "dashboard": ("reads", "a verdict"),
    "log_snapshot": ("outside", "a log file in the app's own folder"),
    "channel_setup": (
        "held",
        f"{_SEAMS}.test_the_channels_account_is_not_created_while_another_yulon_holds_the_server",
    ),
    "database_alone": (
        "open",
        "T604: starts the database alone; held only where Backup/Restore call it, under theirs",
    ),
    "bots": ("reads", "the Browse list"),
    "console_probe": ("reads", "asks the console"),
    "uninstall": (
        "held",
        f"{_HERE}.test_an_uninstall_while_another_yulon_holds_the_server_removes_nothing",
    ),
    "accounts": (
        "held",
        f"{_MORE}.test_an_account_write_while_another_yulon_holds_the_server_sends_nothing",
    ),
    "my_party": (
        "held",
        f"{_SEAMS}.test_a_link_while_another_yulon_holds_the_server_writes_nothing",
    ),
    "play": (
        "held",
        f"{_MORE}.test_a_character_write_while_another_yulon_holds_the_server_sends_nothing",
    ),
    "bot_dashboard": ("reads", "counts"),
    "bot_pool_rebuild": (
        "held",
        f"{_SEAMS}.test_a_pool_rebuild_while_another_yulon_holds_the_server_changes_nothing",
    ),
    "steam": ("outside", "Steam shortcuts, not the server"),
    "ready_after_start": ("reads", "waits for the world after a Start"),
    "module_sql": (
        "held",
        f"{_HERE}.test_apply_module_sql_is_run_under_the_hold_by_the_shared_assembly",
    ),
    "module_updates": ("reads", "what could be updated"),
    "module_notes": ("reads", "notes"),
    "module_version": ("reads", "a version"),
    "installed_modules": ("reads", "a listing"),
    "unfinished_modules": ("reads", "a listing"),
    "unknown_modules": ("reads", "a listing"),
    "module_from_link": ("reads", "reads a manifest"),
    "module_from_folder": ("reads", "reads a manifest"),
    "module_install_custom": ("lower", "an Applier install: the Applier holds"),
    "module_replacement_question": ("reads", "a question"),
    "module_forget": ("outside", "forgets a record in the app's own state"),
    "rebuild": ("lower", "an engine press (native.py reserves)"),
    "rebuild_refusal": ("reads", "a refusal"),
    "updates": ("lower", "an engine press (native.py reserves)"),
    "repair_compose": ("lower", "an engine press (native.py reserves)"),
    "repair_confs": ("lower", "install_wiring reserves it with engine.reserved"),
    "lock_folder": ("outside", "the folder's Windows ACL, not its data"),
    "kept_build": ("lower", "an engine press (native.py reserves)"),
    "repair_database": ("lower", "an engine press (native.py reserves)"),
    "corrections": ("lower", "an engine press (native.py reserves)"),
    "update_to_latest": ("lower", "an engine press (native.py reserves)"),
    "adopt": ("lower", "an engine press (native.py reserves)"),
    "client_dir": ("outside", "the game client's folder"),
    "play_client_dir": ("outside", "the game client's folder"),
    "time_zone": (
        "held",
        f"{_HERE}.test_the_time_zone_write_while_another_yulon_holds_the_server_writes_nothing",
    ),
    "bot_population": (
        "held",
        f"{_HERE}.test_the_bot_count_write_while_another_yulon_holds_the_server_writes_nothing",
    ),
    "reset_settings": (
        "held",
        f"{_HERE}.test_reset_to_default_while_another_yulon_holds_the_server_writes_nothing",
    ),
    "set_client_dir": ("outside", "the app's own state"),
    "set_play_client_dir": ("outside", "the app's own state"),
    "other_server_dirs": ("reads", "a listing"),
    "pathfinding": (
        "held",
        "tests.test_pathfinding_hold."
        "test_the_assembled_start_is_refused_while_another_yulon_holds_the_server",
    ),
    "world_upkeep": (
        "lower",
        "reextract and finish_world_reimport carry @_reserving "
        "(test_the_world_update_and_the_map_extraction_are_presses_of_the_engine_that_reserve)",
    ),
    "characters_withheld": ("reads", "a mapping of reasons"),
    "no_modules_note": ("reads", "a sentence"),
    # Fields of the after-release batch, classified when it was combined with this table:
    "custom_module_tips": ("reads", "two tooltips"),
    "default_addons": (
        "lower",
        "an Applier install: the Applier holds; Play's put-back writes only the game client",
    ),
    "module_refresh": ("reads", "the same counts as module_updates, in the background"),
    "move": (
        "held",
        "tests.test_move_flows.test_a_bring_in_while_another_yulon_holds_the_server_loads_nothing",
    ),
    "client_addons": (
        "lower",
        "an Applier install/remove: the tab's applier, or Centurion's add-on-only one, holds",
    ),
    "shelf": (
        "held",
        "tests.test_backup_shelf.test_another_yulon_holding_the_server_refuses_before_the_lease",
    ),
}

OPEN_SEAMS = {"backup", "restore", "forget_interrupted", "database_alone"}
"""Pinned: closing one of these is an edit here, and a new one is a decision, not a drift."""


def test_every_services_seam_says_what_it_writes() -> None:
    """Mutation: add a field to `ControllerServices` and leave it out of `SEAMS`."""
    fields = {field.name for field in dataclasses.fields(controller_view_module.ControllerServices)}
    assert fields - set(SEAMS) == set(), f"{sorted(fields - set(SEAMS))}: reads, or holds?"
    assert set(SEAMS) - fields == set(), f"{sorted(set(SEAMS) - fields)}: no longer a seam"
    assert {name for name, (kind, _why) in SEAMS.items() if kind == "open"} == OPEN_SEAMS
    assert {kind for kind, _why in SEAMS.values()} <= {"held", "lower", "reads", "outside", "open"}


def test_every_held_seam_names_a_test_that_exists() -> None:
    """Mutation: name a test that is not there, or rename the test the table points at."""
    import importlib

    for name, (kind, where) in SEAMS.items():
        if kind != "held":
            continue
        module, _, test = where.rpartition(".")
        assert callable(getattr(importlib.import_module(module), test, None)), (name, where)


# ------------------------------------------------------------ the uninstall's reservation image


def _tried(state: Path) -> list[str]:
    log = state / "claim-images.log"
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def _lay_images(state: Path, listed: list[str], ids: dict[str, str]) -> None:
    (state / "images-listed").write_text("\n".join(listed) + "\n", encoding="utf-8")
    known = state / "image-ids"
    known.mkdir(exist_ok=True)
    for ref, ident in ids.items():
        (known / ref.replace("/", "_").replace(":", "_")).write_text(ident, encoding="utf-8")


def _uninstall_refs(server: Path) -> tuple[str, ...]:
    """What `Uninstaller` is built to remove, as the controller computes it (the real refs)."""
    from yulon.catalog import composegen
    from yulon.catalog.native import PARKED_TAG_SUFFIX, ROLLBACK_TAG_SUFFIX

    built = composegen.built_image_refs(CATALOG_WOTLK, server)
    return (
        *built,
        *(ref + PARKED_TAG_SUFFIX for ref in built),
        *(ref + ROLLBACK_TAG_SUFFIX for ref in built),
    )


def test_an_uninstalls_reservation_does_not_run_from_an_image_the_uninstall_removes(
    fake_docker: Path, server: Path
) -> None:
    """With no containers left a reservation could run from this install's own built image, and
    `docker image rm` then refuses it. Mutation: ignore `avoid_images` in `_reservation_images`."""
    refs = _uninstall_refs(server)
    other = "yulon.local/some-other-install:native"
    _lay_images(fake_docker, [*refs, other], {})
    hold = _server_hold_with(server, refs)
    with hold("Uninstall the server"):
        pass
    assert _tried(fake_docker)[0] == other, _tried(fake_docker)


def test_an_image_the_uninstall_removes_is_avoided_by_id_too(
    fake_docker: Path, server: Path
) -> None:
    """A container's `.Image` is an id: if it is the id of a ref about to go, it is not used."""
    refs = _uninstall_refs(server)
    other = "yulon.local/some-other-install:native"
    _lay_images(fake_docker, [other], {refs[0]: "sha256:aaaa", other: "sha256:bbbb"})
    (fake_docker / "images").mkdir(exist_ok=True)
    spec = CATALOG_WOTLK.container_spec()
    (fake_docker / "images" / spec.world).write_text("sha256:aaaa", encoding="utf-8")
    (fake_docker / "images" / spec.db).write_text("sha256:cccc-db", encoding="utf-8")
    # The database container's own (pulled) image is fine and first; remove it to test the world's.
    (fake_docker / "images" / spec.db).unlink()
    hold = _server_hold_with(server, refs)
    with hold("Uninstall the server"):
        pass
    assert _tried(fake_docker)[0] == other, _tried(fake_docker)


def test_when_only_the_installs_own_images_are_left_the_uninstall_is_still_reserved(
    fake_docker: Path, server: Path
) -> None:
    """A broken install must still be removable: the last resort is its own image."""
    refs = _uninstall_refs(server)
    _lay_images(fake_docker, list(refs), {})
    hold = _server_hold_with(server, refs)
    with hold("Uninstall the server"):
        pass
    assert _tried(fake_docker)[0] in refs


def _server_hold_with(server: Path, refs: tuple[str, ...]) -> Any:
    from yulon.ui.controller_view import _server_hold_for

    return _server_hold_for(
        CATALOG_WOTLK,
        server,
        CATALOG_WOTLK.container_spec(),
        wsl_distro=None,
        avoid_images=refs,
    )


def test_every_uninstaller_hold_names_the_images_the_uninstall_removes() -> None:
    """Mutation: build one `purge.Uninstaller(`'s hold without `avoid_images=`."""
    path = Path(controller_view_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("Uninstaller"):
            hold = next((k.value for k in node.keywords if k.arg == "hold_server"), None)
            assert hold is not None
            found.append("avoid_images" in {k.arg for k in getattr(hold, "keywords", [])})
    assert len(found) >= 3 and all(found), found


# ------------------------------------------------------------------ the rows that said "lower"


def test_apply_module_sql_is_run_under_the_hold_by_the_shared_assembly(
    fake_docker: Path, server: Path
) -> None:
    """The importer's `compose run` is no Applier path. Mutation: pass `module_sql` unwrapped."""
    ran: list[object] = []

    def importer(output: Any) -> Any:
        ran.append(output)
        return "run"

    services = controller_view_module._assemble(
        CATALOG_WOTLK,
        server,
        client_dir=None,
        wsl_distro=None,
        controller=_Nothing(),  # type: ignore[arg-type]
        sql=_Nothing(),  # type: ignore[arg-type]
        send_console=lambda _c: None,  # type: ignore[arg-type,return-value]
        create_account=lambda *_a: None,  # type: ignore[arg-type,return-value]
        store=None,
        applier=None,
        backup=lambda: None,  # type: ignore[arg-type,return-value]
        plan_restore=lambda *_a: None,  # type: ignore[arg-type,return-value]
        restore=lambda _p: None,  # type: ignore[arg-type,return-value]
        module_sql=importer,
    )
    assert services.module_sql is not None
    assert services.module_sql(print) == "run" and ran == [print], "control: it runs unheld"
    ran.clear()
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        with pytest.raises(docker.ServerReserved, match="Apply module SQL"):
            services.module_sql(print)
    finally:
        theirs.kill()
    assert ran == []


def test_the_world_update_and_the_map_extraction_are_presses_of_the_engine_that_reserve() -> None:
    """`world_upkeep`'s two presses. Mutation: take `@_reserving` off `finish_world_reimport`."""
    from yulon.catalog.families.trinitycore import TrinityCoreInstaller

    for name in ("reextract", "finish_world_reimport"):
        assert hasattr(getattr(TrinityCoreInstaller, name), "__wrapped__"), f"{name} is unreserved"


@pytest.mark.parametrize(
    "game_id", [e.id for e in load_catalog().games if e.client.addon_interface is not None]
)
def test_every_client_addon_route_writes_under_a_server_hold(game_id: str, tmp_path: Path) -> None:
    """The add-on route's applier carries the hold seam on every game, Centurion's own included.

    Mutation this catches: `_with_client_addons()` building the add-on-only applier without
    `hold_server=` (its writes, and the per-server asides note, then run unheld).
    """
    services = controller_view_module.ControllerServices.for_entry(
        load_catalog().get(game_id), tmp_path
    )
    assert services.client_addons is not None
    assert services.client_addons.applier._hold_server is not None, game_id
