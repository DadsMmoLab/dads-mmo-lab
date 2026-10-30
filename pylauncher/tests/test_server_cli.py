"""Tests for `yulon.server_cli`: `yulon --server start|stop|status` for the Gaming Mode tiles.

The controller and the services are fakes, so nothing reaches Docker: what these prove is the
verb's order of calls, the one-game-at-a-time stop, the ready wait, every refusal as a line
of text with its exit code, and which install a game id and a folder pick.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import server_cli
from yulon.catalog.native import StartAnswer
from yulon.controller import InstallStatus, PortConflictError, StartRefused


@dataclass
class FakeController:
    world_up: bool = False
    conflict: bool = False
    refuse: str = ""
    calls: list[str] = field(default_factory=list)
    zone_problem: str | None = None
    world_data_problem: str | None = None
    bot_settings_renamed: str | None = None

    def status(self) -> InstallStatus:
        self.calls.append("status")
        return InstallStatus(db=self.world_up, auth=self.world_up, world=self.world_up)

    def start(self, *, asked_before_the_servers: bool = False) -> None:
        self.calls.append("start")
        if self.refuse:
            raise StartRefused(self.refuse)
        if self.conflict:
            raise PortConflictError(
                ["ac-worldserver"], (3724, 8085), {"ac-worldserver": "/home/deck/yulon-wotlk"}
            )
        self.world_up = True

    def stop_conflicting_and_start(self) -> list[str]:
        self.calls.append("stop_conflicting_and_start")
        self.world_up = True
        return ["ac-worldserver", "ac-authserver"]

    def stop(self, *, by_choice: bool = False) -> bool:
        self.calls.append("stop")
        self.stopped_by_choice = by_choice
        was = self.world_up
        self.world_up = False
        return was


@dataclass
class FakeServices:
    controller: FakeController
    answer: StartAnswer = field(default_factory=lambda: StartAnswer("ready"))
    waited: int = 0

    def __post_init__(self) -> None:
        self.ready_after_start = self._wait

    def _wait(self, **_: Any) -> StartAnswer:
        self.waited += 1
        return self.answer


def _state(config: Path, *installs: tuple[str, str]) -> Path:
    config.mkdir(parents=True, exist_ok=True)
    (config / "state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "installs": [
                    {"game": game, "server_dir": folder, "client_dir": None}
                    for game, folder in installs
                ],
            }
        ),
        encoding="utf-8",
    )
    return config


def _run(
    argv: list[str], config: Path, services: FakeServices
) -> tuple[int, list[str], list[tuple[Any, ...]]]:
    said: list[str] = []
    built: list[tuple[Any, ...]] = []

    def services_for(entry: Any, server_dir: Path, **kwargs: Any) -> FakeServices:
        built.append((entry.id, server_dir, kwargs))
        return services

    code = server_cli.run(
        argv, say=said.append, config_dir=config, services_for=services_for, tick=0.01
    )
    return code, said, built


@pytest.fixture
def config(tmp_path: Path) -> Path:
    return _state(tmp_path / "yulon", ("wow-wotlk", "/home/deck/yulon-wotlk"))


def test_start_runs_the_tabs_start_then_waits_for_the_world(config: Path) -> None:
    services = FakeServices(FakeController())
    code, said, built = _run(["start", "wow-wotlk"], config, services)
    assert code == server_cli.EXIT_OK
    assert services.controller.calls == ["start"]
    assert services.waited == 1
    assert said[-1] == "WoW WotLK is ready."
    ((game, folder, kwargs),) = built
    assert game == "wow-wotlk" and folder == Path("/home/deck/yulon-wotlk")
    assert set(kwargs) == {"client_dir", "wsl_distro", "play_client_dir"}


def test_a_server_holding_the_ports_is_stopped_first_and_named(config: Path) -> None:
    services = FakeServices(FakeController(conflict=True))
    code, said, _ = _run(["start", "wow-wotlk"], config, services)
    assert code == server_cli.EXIT_OK
    assert services.controller.calls == ["start", "stop_conflicting_and_start"]
    assert "The install in /home/deck/yulon-wotlk holds the ports" in said[0]
    assert "one game at a time" in said[0]
    assert said[1] == "Stopped ac-worldserver, ac-authserver."


def test_a_world_that_does_not_report_ready_is_exit_3_with_its_words(config: Path) -> None:
    services = FakeServices(FakeController(), StartAnswer("crashed", "worldserver: oops"))
    code, said, _ = _run(["start", "wow-wotlk"], config, services)
    assert code == server_cli.EXIT_NOT_READY
    assert "did not report ready (crashed)" in said[-2] and said[-1] == "worldserver: oops"


def test_a_refused_start_prints_yulons_sentence_and_waits_for_nothing(config: Path) -> None:
    services = FakeServices(FakeController(refuse="The database is missing. Reinstall it."))
    code, said, _ = _run(["start", "wow-wotlk"], config, services)
    assert code == server_cli.EXIT_REFUSED
    assert said == ["The database is missing. Reinstall it."]
    assert services.waited == 0


def test_what_the_start_could_not_put_right_is_said(config: Path) -> None:
    controller = FakeController(zone_problem="the zone file could not be put back")
    code, said, _ = _run(["start", "wow-wotlk"], config, FakeServices(controller))
    assert code == server_cli.EXIT_OK
    assert "the zone file could not be put back" in said


def test_stop_and_status(config: Path) -> None:
    services = FakeServices(FakeController(world_up=True))
    assert _run(["status", "wow-wotlk"], config, services)[:2] == (0, ["running"])
    assert _run(["stop", "wow-wotlk"], config, services)[:2] == (0, ["WoW WotLK stopped."])
    # T826: the player's own Stop, so the install's "When stopping" choice reaches it.
    assert services.controller.stopped_by_choice is True  # type: ignore[attr-defined]
    assert _run(["status", "wow-wotlk"], config, services)[:2] == (1, ["stopped"])
    assert _run(["stop", "wow-wotlk"], config, services)[:2] == (0, ["WoW WotLK was not running."])


def test_the_folder_picks_the_install_and_two_installs_need_one(tmp_path: Path) -> None:
    config = _state(
        tmp_path / "yulon", ("wow-wotlk", "/home/deck/a"), ("wow-wotlk", "/home/deck/b")
    )
    services = FakeServices(FakeController())
    code, said, _ = _run(["status", "wow-wotlk"], config, services)
    assert code == server_cli.EXIT_REFUSED and "more than one wow-wotlk install" in said[0]
    code, _, built = _run(["status", "wow-wotlk", "/home/deck/b"], config, services)
    assert built[0][1] == Path("/home/deck/b")


@pytest.mark.parametrize(
    ("argv", "code", "words"),
    [
        ([], server_cli.EXIT_USAGE, "usage:"),
        (["restart", "wow-wotlk"], server_cli.EXIT_USAGE, "usage:"),
        (["start", "no-such-game"], server_cli.EXIT_USAGE, "not a game this Yu'lon can run"),
        (["start", "wow-vanilla"], server_cli.EXIT_REFUSED, "has no wow-vanilla install"),
    ],
)
def test_a_wrong_command_line_says_so_and_builds_nothing(
    config: Path, argv: list[str], code: int, words: str
) -> None:
    services = FakeServices(FakeController())
    got, said, built = _run(argv, config, services)
    assert got == code and words in said[0]
    assert built == [] and services.controller.calls == []
