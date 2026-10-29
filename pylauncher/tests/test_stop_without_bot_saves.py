"""A Stop can be set not to wait for the bots' saves; players are always saved first (T826).

Live, the CoA proof (2026-10-28): a Stop before a realm switch took 2 to 25 minutes, the world
logging `Waiting for 2929 queries to finish`. Read at the CoA core: on a signal AzerothCore runs
`OnShutdown()` first (playerbots logs out every bot, each a logout save) and only then kicks the
real players, so their saves queue behind every bot's. So the fast Stop saves every player and
SEES the character queue empty while the world still runs, and only then cuts the wait after the
signal short -- the world alone, never the database.

Each behaviour is pinned through a real stop path with only `runner.run` and the console faked,
on the harness of `test_stop_saves_before_exit.py`:

* the default waits (no choice made, a choice that cannot be read, the catalog's default);
* the fast Stop is bounded at the catalog's seconds and kills the world only;
* every stop the player's choice does not reach waits, whatever the choice says;
* players are saved, and seen written, before the signal; a save not seen written waits.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from tests.test_controller_view import _inline_jobs  # noqa: F401 - an autouse fixture
from tests.test_stop_saves_before_exit import (  # noqa: F401 - `_fast` is an autouse fixture
    _Docker,
    _fast,
    _install,
    _rising,
)
from yulon import docker, stop_choice
from yulon.catalog.catalog import StopWithoutBotSaves, load_catalog
from yulon.controller import Controller

CATALOG = load_catalog()


class _Console:
    """An AzerothCore console: `saveall`, then `server debug` answering each of `queue` in turn."""

    def __init__(self, fake: _Docker, queue: list[int | None]) -> None:
        self.fake = fake
        self.queue = queue
        self.looks = 0
        self.refuse = False

    def __call__(self, command: str, **kw: object) -> object:
        from yulon.controller_wow_wotlk.console import ConsoleError, ConsoleReply

        skippable = self.fake.spec.bot_saves_skippable
        assert skippable is not None
        assert kw["container"] == self.fake.spec.world
        assert kw["prompt"] == skippable.save.prompt
        if self.refuse:
            raise ConsoleError("no attach")
        self.fake.events.append(f"console: {command}")
        if command != "server debug":
            return ConsoleReply(command, ("All players saved.",), prompted=True)
        self.looks += 1
        if self.looks > 1:
            self.fake.now += self.fake.step
        size = self.queue[min(self.looks, len(self.queue)) - 1] if self.queue else None
        lines = ["LoginDatabase queue size: 0"]
        if size is not None:
            lines.append(f"CharacterDatabase queue size: {size}")
        return ConsoleReply(command, tuple(lines), prompted=True)


def _console(monkeypatch: pytest.MonkeyPatch, fake: _Docker, queue: list[int | None]) -> _Console:
    console = _Console(fake, queue)
    monkeypatch.setattr(docker, "_console_send", console)
    monkeypatch.setattr(docker, "_console_reaches", lambda wsl_distro: True)
    return console


def _stop(
    fake: _Docker, server_dir: Path, *, by_choice: bool = True
) -> tuple[list[str], Controller]:
    said: list[str] = []
    controller = Controller(fake.spec, server_dir)
    controller.stop_control = docker.StopControl(say=said.append)
    controller.stop(by_choice=by_choice)
    return said, controller


def _not_waiting(server_dir: Path) -> None:
    stop_choice.choose(server_dir, False)


LONG = _rising(docker.STOP_GRACE_SECONDS)
"""A world that is still writing the bots' saves five minutes after its signal."""

SECONDS = 20.0


# -- the catalog: which games offer it --------------------------------------------------------


def test_the_azerothcore_games_offer_it_and_the_rest_do_not() -> None:
    """AzerothCore's long wait is after the signal and its queue can be read: offered, on
    Conquest of Azeroth too (its core is AzerothCore with the same `server debug` line, and its
    live Stop is where the 2929-query wait was seen). CMaNGOS has no console line that reads its
    queue, so a cut could not prove the players written; Centurion's whole wait is the save it
    asks for first, because its close drops the queue."""
    for game in ("wow-wotlk", "wow-unbound", "wow-coa"):
        skippable = CATALOG.get(game).container_spec().bot_saves_skippable
        assert skippable is not None, game
        assert skippable.save.command == "saveall"
        assert skippable.save.queue_command == "server debug"
        assert skippable.save.queue_length("CharacterDatabase queue size: 2929") == 2929
        assert skippable.seconds == SECONDS
        assert skippable.waits_by_default is True
    for game in ("wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"):
        assert CATALOG.get(game).container_spec().bot_saves_skippable is None, game


def test_the_catalog_refuses_a_cut_beside_a_save_first_and_a_pattern_without_one_group() -> None:
    from pydantic import ValidationError

    from yulon.catalog.catalog import NativeInstall

    centurion = CATALOG.get("wow-centurion").install.native
    assert centurion is not None
    data = centurion.model_dump(mode="json", exclude_none=True)
    data["stop_without_bot_saves"] = {"queue_command": "server debug", "queue_pattern": r"(\d+)"}
    with pytest.raises(ValidationError, match="save_before_stop"):
        NativeInstall.model_validate(data)
    for bad in (
        {"queue_command": "server debug", "queue_pattern": r"\d+"},
        {"queue_command": "server debug", "queue_pattern": r"(a)(b)"},
        {"queue_command": "", "queue_pattern": r"(\d+)"},
        {"queue_command": "server debug", "queue_pattern": r"(\d+)", "seconds": 1},
    ):
        with pytest.raises(ValidationError):
            StopWithoutBotSaves(**bad)  # type: ignore[arg-type]


# -- the record -------------------------------------------------------------------------------


def test_the_choice_is_kept_per_install_and_anything_unreadable_is_the_default(
    tmp_path: Path,
) -> None:
    assert stop_choice.waits_for_bot_saves(tmp_path) is True
    assert stop_choice.waits_for_bot_saves(tmp_path, default=False) is False
    stop_choice.choose(tmp_path, False)
    assert stop_choice.waits_for_bot_saves(tmp_path) is False
    record = json.loads((tmp_path / stop_choice.RECORD_FILE).read_text(encoding="utf-8"))
    assert record == {"schema_version": 1, "waits_for_bot_saves": False}
    stop_choice.choose(tmp_path, True)
    assert stop_choice.waits_for_bot_saves(tmp_path, default=False) is True
    for broken in ("{", '{"waits_for_bot_saves": "no"}', "[]", ""):
        (tmp_path / stop_choice.RECORD_FILE).write_text(broken, encoding="utf-8")
        assert stop_choice.waits_for_bot_saves(tmp_path) is True, broken
    assert [p.name for p in tmp_path.iterdir()] == [stop_choice.RECORD_FILE]


def test_a_choice_that_cannot_be_written_raises_and_leaves_the_old_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stop_choice.choose(tmp_path, True)

    def refuse(_src: object, _dst: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(stop_choice.os, "replace", refuse)
    with pytest.raises(OSError):
        stop_choice.choose(tmp_path, False)
    assert stop_choice.waits_for_bot_saves(tmp_path) is True
    assert [p.name for p in tmp_path.iterdir()] == [stop_choice.RECORD_FILE]


# -- the default is the safe Stop -------------------------------------------------------------


@pytest.mark.parametrize("record", [None, True, "garbage"])
def test_without_a_choice_to_skip_the_stop_waits_for_every_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, record: object
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", LONG)
    console = _console(monkeypatch, fake, [0])
    if record is True:
        stop_choice.choose(tmp_path, True)
    elif record == "garbage":
        (tmp_path / stop_choice.RECORD_FILE).write_text("{", encoding="utf-8")
    said, _ = _stop(fake, tmp_path)
    assert fake.events == ["SIGTERM", "world exited by itself", "compose stop (world down)"]
    assert console.looks == 0
    assert said == [docker.WORLD_SAVING, docker.WORLD_SAVED]


def test_a_stop_control_waits_unless_told_otherwise() -> None:
    assert docker.StopControl().skip_bot_saves is False


def test_a_catalog_default_that_does_not_wait_is_the_choice_until_one_is_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", LONG)
    skippable = fake.spec.bot_saves_skippable
    assert skippable is not None
    fake.spec = dataclasses.replace(
        fake.spec, bot_saves_skippable=dataclasses.replace(skippable, waits_by_default=False)
    )
    assert Controller(fake.spec, tmp_path).skips_bot_saves() is True
    stop_choice.choose(tmp_path, True)
    assert Controller(fake.spec, tmp_path).skips_bot_saves() is False


# -- the fast Stop: players first, then bounded -----------------------------------------------


def test_not_waiting_saves_every_player_then_kills_the_world_alone_at_the_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", LONG)
    console = _console(monkeypatch, fake, [900, 300, 0])
    _not_waiting(tmp_path)
    said, _ = _stop(fake, tmp_path)
    assert fake.events == [
        "console: saveall",
        *["console: server debug"] * 3,
        "SIGTERM",
        "SIGKILL",
        "compose stop (world down)",
    ]
    assert console.looks == 3
    assert fake.termed_at is not None
    assert SECONDS <= fake.now - fake.termed_at < SECONDS + 2 * fake.step
    assert said == [
        docker.SAVE_FIRST_ASKING,
        docker.SAVE_FIRST_QUEUED.format(count=900),
        docker.SAVE_FIRST_WRITTEN,
        docker.world_closing_without_bot_saves(SECONDS),
        docker.WORLD_STOPPED_WITHOUT_BOT_SAVES,
    ]
    assert docker.WORLD_STOPPED_WITHOUT_BOT_SAVES not in docker.FORCE_STOP_WARNINGS


def test_the_kill_is_the_world_alone_and_the_database_gets_its_ordinary_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", LONG)
    killed: list[list[str]] = []
    real = fake.__call__

    def watch(cmd: list[str], **kw: object) -> object:
        if cmd[1:2] == ["kill"]:
            killed.append(cmd[1:])
        return real(cmd, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(docker.runner, "run", watch)
    _console(monkeypatch, fake, [0])
    _not_waiting(tmp_path)
    _stop(fake, tmp_path)
    assert killed == [["kill", "-s", "TERM", fake.spec.world], ["kill", fake.spec.world]]
    assert fake.events[-1] == "compose stop (world down)"


def test_a_world_that_closes_inside_the_bound_is_said_to_have_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(SECONDS / 2))
    _console(monkeypatch, fake, [0])
    _not_waiting(tmp_path)
    said, _ = _stop(fake, tmp_path)
    assert "SIGKILL" not in fake.events
    assert said[-1] == docker.WORLD_SAVED


@pytest.mark.parametrize(
    "queue, refuse",
    [([None], False), ([5000], False), ([0], True)],
    ids=["queue-unreadable", "queue-never-empties", "console-unreachable"],
)
def test_a_players_save_not_seen_written_falls_back_to_the_full_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: list[int | None],
    refuse: bool,
) -> None:
    """Players are never cut short: a save that could not be seen written waits as always."""
    fake = _install(monkeypatch, "wow-wotlk", LONG)
    console = _console(monkeypatch, fake, queue)
    console.refuse = refuse
    _not_waiting(tmp_path)
    said, _ = _stop(fake, tmp_path)
    assert "SIGKILL" not in fake.events
    assert fake.events[-2:] == ["world exited by itself", "compose stop (world down)"]
    assert docker.PLAYERS_SAVE_UNSEEN in said
    assert said[-2:] == [docker.WORLD_SAVING, docker.WORLD_SAVED]
    for warning in (docker.SAVE_FIRST_NOT_ASKED, docker.SAVE_FIRST_UNFINISHED):
        assert warning not in said, "a fallback that loses nothing warned that it might"


def test_the_players_save_is_bounded_before_it_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", LONG)
    queue = list(range(5000, 0, -1))  # always shrinking, never empty in time
    _console(monkeypatch, fake, queue)  # type: ignore[arg-type]
    _not_waiting(tmp_path)
    _stop(fake, tmp_path)
    assert fake.termed_at is not None
    ceiling = docker.PLAYERS_SAVE_CEILING_SECONDS
    assert ceiling <= fake.termed_at < ceiling + 2 * fake.step


# -- every stop the choice does not reach waits -----------------------------------------------


def test_a_stop_not_pressed_by_the_player_waits_whatever_the_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A move, a realm switch, a bot restart: `Controller.stop()` without `by_choice`."""
    fake = _install(monkeypatch, "wow-wotlk", LONG)
    console = _console(monkeypatch, fake, [0])
    _not_waiting(tmp_path)
    said, _ = _stop(fake, tmp_path, by_choice=False)
    assert "SIGKILL" not in fake.events
    assert console.looks == 0
    assert said == [docker.WORLD_SAVING, docker.WORLD_SAVED]


def test_the_coa_realm_switch_stop_waits_whatever_the_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The realm switch's own Stop (T812 C3b) on Conquest of Azeroth, which offers the choice:
    it reloads the characters schema under the next realm, so it never reads the choice and
    waits for every save. Driven through the switch's real stop, not a stand-in."""
    from yulon.ui import controller_view

    fake = _install(monkeypatch, "wow-coa", LONG)
    console = _console(monkeypatch, fake, [0])
    _not_waiting(tmp_path)
    assert Controller(fake.spec, tmp_path).skips_bot_saves() is True
    controller_view._stop_for_realm_switch(CATALOG.get("wow-coa"), tmp_path, wsl_distro=None)
    assert fake.termed_at is not None, "the switch's stop never signalled the world"
    assert "SIGKILL" not in fake.events
    assert console.looks == 0


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-coa"])
@pytest.mark.parametrize("route", ["remove", "restart", "rebuild"])
def test_the_routes_that_need_the_database_whole_wait_whatever_the_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, route: str, game: str
) -> None:
    """A removal, the controller's own restart (the Tortoise bot rebuild's), and the stop of a
    rebuild or update (`stop_servers_staged`) never read the choice; on Conquest of Azeroth
    too, whose uninstall and Rebuild run these same stops."""
    fake = _install(monkeypatch, game, LONG)
    console = _console(monkeypatch, fake, [0])
    _not_waiting(tmp_path)
    controller = Controller(fake.spec, tmp_path)
    controller.stop_control = docker.StopControl()
    monkeypatch.setattr(Controller, "start", lambda self, **kw: None)
    monkeypatch.setattr(Controller, "refuse_before_a_stop", lambda self: None)
    if route == "remove":
        controller.remove()
    elif route == "restart":
        controller.restart()
    else:
        docker.stop_servers_staged(fake.spec, tmp_path, control=docker.StopControl())
    assert "SIGKILL" not in fake.events
    assert console.looks == 0


def test_a_game_that_does_not_offer_it_waits_whatever_its_record_says(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-vanilla", _rising(60))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    _not_waiting(tmp_path)
    assert Controller(fake.spec, tmp_path).skips_bot_saves() is False
    said, _ = _stop(fake, tmp_path)
    assert "SIGKILL" not in fake.events
    assert not [e for e in fake.events if e.startswith("console")]
    assert said[-1] == docker.WORLD_SAVED


def test_the_choice_rides_on_a_copy_that_shares_the_tabs_events(tmp_path: Path) -> None:
    """Stop now anyway and the give-up still reach a Stop the choice made fast."""
    spec = CATALOG.get("wow-wotlk").container_spec()
    controller = Controller(spec, tmp_path)
    control = docker.StopControl()
    controller.stop_control = control
    _not_waiting(tmp_path)
    fast = controller._control_for(True)
    assert fast is not None and fast.skip_bot_saves is True
    assert fast.anyway is control.anyway and fast.abandon is control.abandon
    assert controller._control_for(False) is control
    assert control.skip_bot_saves is False, "the tab's own control was changed"


def test_the_player_text_names_no_command() -> None:
    from tests.support_player_text import command_faults

    for text in (
        stop_choice.WAIT_LABEL,
        stop_choice.NO_WAIT_LABEL,
        stop_choice.CHOICE_NOTE,
        stop_choice.choice_tip(SECONDS),
        stop_choice.CHOICE_NOT_KEPT.format(why="read-only"),
        docker.PLAYERS_SAVE_UNSEEN,
        docker.WORLD_STOPPED_WITHOUT_BOT_SAVES,
        docker.world_closing_without_bot_saves(SECONDS),
    ):
        assert not command_faults(text), (text, command_faults(text))


# -- the Server tab ---------------------------------------------------------------------------


def _view(ps: object, tmp_path: Path, game: str = "wow-wotlk") -> object:
    from tests.test_controller_view import WOTLK, _services
    from yulon.ui.controller_view import ControllerView

    services = _services(ps, tmp_path, [])  # type: ignore[arg-type]
    if game != "wow-wotlk":
        services = dataclasses.replace(
            services, controller=Controller(CATALOG.get(game).container_spec(), tmp_path)
        )
    return ControllerView(WOTLK, services, status_poll_ms=0)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> object:
    from tests.test_controller_view import _Ps
    from yulon import runner

    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def test_the_server_tab_offers_the_choice_under_stop_and_keeps_what_is_picked(
    qapp: object, ps: object, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path)
    box = view.stop_choice_box  # type: ignore[attr-defined]
    assert not box.isHidden() and not view.stop_choice_note.isHidden()  # type: ignore[attr-defined]
    assert [box.itemText(i) for i in range(box.count())] == [
        stop_choice.WAIT_LABEL,
        stop_choice.NO_WAIT_LABEL,
    ]
    assert box.currentIndex() == 0, "the safe Stop is not the default"
    assert "backup" in box.toolTip() and "20 seconds" in box.toolTip()
    box.setCurrentIndex(1)
    assert stop_choice.waits_for_bot_saves(tmp_path) is False
    assert _view(ps, tmp_path).stop_choice_box.currentIndex() == 1  # type: ignore[attr-defined]
    box.setCurrentIndex(0)
    assert stop_choice.waits_for_bot_saves(tmp_path, default=False) is True


def test_a_game_that_does_not_offer_it_shows_no_choice(
    qapp: object, ps: object, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path, "wow-vanilla")
    for widget in (view.stop_choice_box, view.stop_choice_label, view.stop_choice_note):  # type: ignore[attr-defined]
        assert widget.isHidden()


def test_a_choice_that_cannot_be_kept_is_said_and_put_back(
    monkeypatch: pytest.MonkeyPatch, qapp: object, ps: object, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path)

    def refuse(_server_dir: Path, _waits: bool) -> None:
        raise OSError("read-only folder")

    monkeypatch.setattr(stop_choice, "choose", refuse)
    view.stop_choice_box.setCurrentIndex(1)  # type: ignore[attr-defined]
    assert view.stop_choice_box.currentIndex() == 0  # type: ignore[attr-defined]
    assert "could not keep" in view.stop_choice_note.text()  # type: ignore[attr-defined]


def test_the_players_stop_and_restart_presses_carry_the_choice(
    monkeypatch: pytest.MonkeyPatch, qapp: object, ps: object, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path)
    controller = view.services.controller  # type: ignore[attr-defined]
    asked: list[bool] = []

    def stop(*, by_choice: bool = False) -> bool:
        asked.append(by_choice)
        return True

    monkeypatch.setattr(controller, "stop", stop)
    monkeypatch.setattr(controller, "start", lambda **kw: None)
    monkeypatch.setattr(controller, "refuse_before_a_stop", lambda: None)
    view.stop_server()  # type: ignore[attr-defined]
    view._do_restart()  # type: ignore[attr-defined]
    assert asked == [True, True]
