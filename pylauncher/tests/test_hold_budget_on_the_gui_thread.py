"""T610 review: a hold taken on the GUI thread is bounded, so a slow Docker cannot freeze it.

The Tuning saves, the channel's roll-back (a failed Start's slot) and the Server tab's channel
Repair run on the GUI thread. A take without a budget waits out Docker's image look-ups and the
per-name lock (a minute and more). `docker.GUI_HOLD_BUDGET_SECONDS` bounds each; a take that runs
out is the "could not reserve" sentence, with nothing written. The fake daemon answers `inspect`
in 3 s (`inspect-hangs`), three look-ups per take, and the budget is cut to 0.8 s here.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from tests.test_install_channel import INSTALL, WOTLK, _installed, _save, _Scripted
from yulon import channel_setup as setup
from yulon import docker, platform, resources
from yulon.ui import controller_view as cv

BUDGET = 0.8
LIMIT = 2.5
"""Budget plus slack for a loaded runner; an unbudgeted take runs 9 s and more."""


@pytest.fixture
def slow_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    monkeypatch.setattr(docker, "GUI_HOLD_BUDGET_SECONDS", BUDGET)
    (state / "images-listed").write_text("yulon.local/wotlk-server:native\n", encoding="utf-8")
    (state / "inspect-hangs").write_text("", encoding="utf-8")
    yield state
    end_fake_containers(state)


def _within(run: Any) -> float:
    """Elapsed seconds of `run()` on a thread; fails if it is still going at 4 x the limit."""
    began = time.monotonic()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(LIMIT * 4)
    assert not worker.is_alive(), "the GUI-thread hold is not bounded"
    return time.monotonic() - began


def test_the_tuning_saves_hold_gives_up_inside_its_budget_on_a_slow_docker(
    slow_docker: Path, tmp_path: Path
) -> None:
    """Mutation: drop `budget=` where `_assemble` builds `hold_server` and the take runs 9 s."""
    server = tmp_path / "server"
    server.mkdir()
    services = cv._assemble(
        WOTLK,
        server,
        client_dir=None,
        wsl_distro=None,
        controller=None,  # type: ignore[arg-type]
        sql=None,  # type: ignore[arg-type]
        send_console=lambda _c: None,  # type: ignore[arg-type,return-value]
        create_account=lambda *_a: None,  # type: ignore[arg-type,return-value]
        store=None,
        applier=None,
        backup=lambda: None,  # type: ignore[arg-type,return-value]
        plan_restore=lambda *_a: None,  # type: ignore[arg-type,return-value]
        restore=lambda _p: None,  # type: ignore[arg-type,return-value]
    )
    assert services.hold_server is not None
    outcome: list[BaseException | None] = []

    def save() -> None:
        try:
            with services.hold_server("Save settings"):
                outcome.append(None)
        except docker.ServerHeldError as refused:
            outcome.append(refused)

    assert _within(save) < LIMIT
    assert isinstance(outcome[0], docker.ServerHeldError), "the take ran out: nothing is written"


def _channel(tmp_path: Path, server: Path) -> setup.InstallChannel:
    """A channel whose hold is the controller's own and has no budget of its own."""
    return setup.InstallChannel(
        WOTLK,
        server,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *_a: None,
        reset=lambda *_a: None,
        channel_for=lambda _e: _Scripted(["no"]),
        config_dir=tmp_path / "config",
        hold_server=cv._server_hold_for(WOTLK, server, WOTLK.container_spec(), wsl_distro=None),
    )


def test_the_channels_roll_back_gives_up_inside_the_budget_on_a_slow_docker(
    slow_docker: Path, tmp_path: Path
) -> None:
    """A failed Start's slot calls it on the GUI thread. Mutation: no budget in `roll_back`."""
    server = _installed(tmp_path)
    channel = _channel(tmp_path, server)
    outcome: list[BaseException | None] = []

    def roll_back() -> None:
        try:
            channel.roll_back()
            outcome.append(None)
        except setup.ServerHeldElsewhere as held:
            outcome.append(held)

    assert _within(roll_back) < LIMIT
    assert isinstance(outcome[0], setup.ServerHeldElsewhere), "the take ran out: nothing undone"


def test_the_channels_repair_gives_up_inside_the_budget_on_a_slow_docker(
    slow_docker: Path, tmp_path: Path
) -> None:
    """The Server tab's Repair button calls it on the GUI thread. Mutation: no budget in repair."""
    _save(tmp_path, password="stale")
    server = _installed(tmp_path)
    channel = _channel(tmp_path, server)
    channel.check()
    states: list[Any] = []
    assert _within(lambda: states.append(channel.repair())) < LIMIT
    assert states, "the repair did not answer"
