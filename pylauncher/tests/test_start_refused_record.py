"""T197 fix round 2: `native.START_REFUSED_FILE` refuses every start, on every family.

A rollback that left the image tags mixed leaves no build a start may run: in that
geometry `compose up -d` would start the NEW import image beside the OLD world server.
The record is in the server folder, so it outlives the app, and `Controller.refuse_start()`
-- the one door every Start, Start and play, the launcher's PLAY, Restart and Recreate
goes through -- reads it before the family's own `start_guard`. Each press is driven
here over a folder holding the record, and none may start, stop or remove anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import test_rebuild_random_bots as random_bots
from tests import test_tortoise_bot_pool as bot_pool
from tests.test_controller_view import (
    WOTLK,
    _answer,
    _built,
    _game_client,
    _play_view,
    _Ps,
    _services,
)
from tests.test_launcher_window import _launcher
from yulon import runner, tuning
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller, StartRefused
from yulon.controller_wow_tortoise import botdash, botpool, poolreset
from yulon.controller_wow_tortoise.controller import TortoiseController
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

REFUSED = native.REBUILD_OWED_REFUSAL


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The view's jobs run inline, as in `test_controller_view`."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    """`test_controller_view`'s Docker-free `runner.run`."""
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every `LaunchSpec` Play hands to `play_launch.launch()`; nothing is started (Windows)."""
    from yulon import play_launch

    monkeypatch.setattr(controller_view_module.platform, "detect", lambda: "windows")
    specs: list[object] = []
    monkeypatch.setattr(play_launch, "launch", lambda spec, **_k: specs.append(spec))
    return specs


def _owed(server_dir: Path) -> None:
    assert native.owe_start(server_dir) == ""


def _touched(ps: _Ps, since: int = 0) -> list[list[str]]:
    """Every compose up, stop, rm or down, and every plain `docker stop`, since `since`."""
    return [
        c
        for c in ps.calls[since:]
        if c[:3]
        in (
            ["docker", "compose", "up"],
            ["docker", "compose", "stop"],
            ["docker", "compose", "rm"],
            ["docker", "compose", "down"],
        )
        or c[:2] == ["docker", "stop"]
    ]


def test_the_record_refuses_a_new_controller_and_goes_once_removed(tmp_path: Path) -> None:
    """The app restarted: a controller made fresh over the folder reads it from disk."""
    _owed(tmp_path)
    with pytest.raises(StartRefused) as refused:
        Controller(WOTLK.container_spec(), tmp_path).refuse_start()
    assert str(refused.value) == REFUSED
    (tmp_path / native.START_REFUSED_FILE).unlink()
    Controller(WOTLK.container_spec(), tmp_path).refuse_start()


def test_the_start_button_starts_nothing_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _owed(tmp_path)
    view.start_server()
    assert view.problem_label.text() == REFUSED
    assert _touched(ps) == []


def test_start_and_play_starts_neither_the_server_nor_the_game(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: list[object]
) -> None:
    _answer(monkeypatch, controller_view_module.QMessageBox.StandardButton.Yes)
    original = _game_client(tmp_path / "clients" / "WoW")
    view, _ = _play_view(ps, tmp_path, original=original, play=_built(original, tmp_path))
    _owed(tmp_path)
    ps.names = ""
    view.play()
    assert _touched(ps) == []
    assert launched == []
    assert view.problem_label.text() == REFUSED


@pytest.mark.parametrize("press", ["restart", "recreate"])
def test_restart_and_recreate_stop_and_remove_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, press: str
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    monkeypatch.setattr(view, "_confirm", lambda title, question: True)
    spec = WOTLK.container_spec()
    ps.names = "".join(f"{n}\n" for n in (spec.db, spec.auth, spec.world))
    _owed(tmp_path)
    since = len(ps.calls)
    view.restart_server() if press == "restart" else view.recreate_containers()
    assert view.tuning_report.toPlainText() == f"FAILED: {REFUSED}"
    assert _touched(ps, since) == []


def test_stopping_the_other_server_to_start_this_one_stops_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _owed(tmp_path)
    view.stop_other_and_start()
    assert REFUSED in view.problem_label.text()
    assert _touched(ps) == []


def test_the_launchers_play_starts_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "exec",
        lambda self: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    window, view, _play = _launcher(ps, tmp_path)
    _owed(tmp_path)
    ps.names = ""
    window.play_button.click()
    assert launched == []
    assert _touched(ps) == []
    assert view.problem_label.text() == REFUSED


def test_a_tortoise_start_is_refused_before_its_bot_dashboard_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tortoise's own `start()` brings the dashboard up first; a refused start must not."""
    asked: list[str] = []
    monkeypatch.setattr(botdash, "start_if_on", lambda *a, **_k: asked.append("dashboard"))
    _owed(tmp_path)
    with pytest.raises(StartRefused, match="must be rebuilt"):
        TortoiseController(tmp_path).start()
    assert asked == []


class _Running(Controller):
    """A real controller over the folder whose server is up: it records any stop or start."""

    def __init__(self, server_dir: Path) -> None:
        super().__init__(TORTOISE.container_spec(), server_dir)
        self.calls: list[str] = []

    def stop(self) -> bool:
        self.calls.append("stop")
        return True

    def start(self) -> None:
        self.calls.append("start")


TORTOISE = load_catalog().get("wow-tortoise")


def test_the_bot_reload_leaves_a_running_server_running_and_says_why(tmp_path: Path) -> None:
    """Fix round 3: `botpool.restart_world()` asks before its stop, as `_do_restart` does.

    The bot reload and the pool rebuild's restart both go through it.
    """
    running = _Running(tmp_path)
    _owed(tmp_path)
    with pytest.raises(StartRefused) as refused:
        botpool.restart_world(running)
    assert str(refused.value) == REFUSED
    assert running.calls == [], "not stopped, so still running"


def test_the_dashboard_switch_restart_leaves_it_running_and_says_why(tmp_path: Path) -> None:
    """The bot dashboard's Yes restarts through the same call; its log says the refusal."""
    running = _Running(tmp_path)
    _owed(tmp_path)
    said = list(botdash.Dashboard(TORTOISE, tmp_path, running).restart_world())
    assert running.calls == []
    assert said[-1] == (
        f"The restart was refused, so the server was not stopped: {REFUSED} The bots module "
        "reads its settings at the next start."
    )
    assert "Restart the server from the Server tab" not in said[-1], "Restart is refused too"


def test_rebuild_random_bots_writes_nothing_and_leaves_the_world_up(tmp_path: Path) -> None:
    """Fix round 4: asked before the backup, the enrolment and the key, not at the restart.

    Asked at the restart, the key was already in aiplayerbot.conf, armed for the first
    start after the repair, and the log watched a run that never read it.
    """
    path = random_bots._conf(tmp_path)
    world = random_bots.World(tmp_path)
    _owed(tmp_path)
    with pytest.raises(poolreset.PoolResetError) as refused:
        world.run(backup=world.backup)
    assert str(refused.value) == (
        f"{REFUSED} The random bots were not rebuilt: nothing was written and the server was "
        "not stopped."
    )
    assert world.events == [], "no backup, no enrolment, no restart"
    assert world.restarts == 0 and world.running is True
    assert path.read_bytes() == random_bots.CONF_TEXT.encode("utf-8")
    assert tuning.backups_of(path) == ()


def test_a_restart_refused_after_the_key_was_written_takes_the_key_back(tmp_path: Path) -> None:
    """The belt: a refusal that arrives at the restart is routed as "nothing was stopped"."""
    random_bots._conf(tmp_path)

    class _Refused(random_bots.World):
        def restart(self) -> None:
            self.restart_tried = True
            raise StartRefused(REFUSED)

    world = _Refused(tmp_path)
    with pytest.raises(poolreset.PoolResetError) as refused:
        world.run()
    assert world.key() == "off", "taken back: the running world never re-reads it"
    assert str(refused.value).startswith(
        f"The restart was refused, so the server was not stopped: {REFUSED} "
    )
    assert "came up" not in str(refused.value)


def test_the_owed_enrolment_restart_says_the_refusal_not_restart(tmp_path: Path) -> None:
    random_bots._conf(tmp_path)
    world = random_bots.World(tmp_path)

    def refuse() -> None:
        raise StartRefused(REFUSED)

    job = poolreset.PoolRebuild(
        entry=TORTOISE,
        server_dir=tmp_path,
        world_running=world.world_running,
        channels=world.channels,  # type: ignore[arg-type]
        restart=refuse,
        world_log=world.world_log,
        clock=world.clock,
        pause=lambda _s, _c=None: None,
    )
    with pytest.raises(poolreset.PoolResetError) as refused:
        list(job.restart_owed_now())
    assert str(refused.value) == (
        f"The restart was refused, so the server was not stopped: {REFUSED} The enrolled bots "
        "log in at any later start."
    )


def test_the_update_adoption_restart_says_the_refusal_not_restart() -> None:
    """`botpool.after_update()`'s restart after an enrolment, refused."""
    soap = bot_pool.ScriptedChannel(
        [bot_pool.yes(bot_pool.PREVIEW_PENDING), bot_pool.yes(bot_pool.CONFIRMED)]
    )

    def refuse() -> None:
        raise StartRefused(REFUSED)

    heads = [bot_pool.OLD, bot_pool.NEW]
    lines = list(
        botpool.after_update(
            lambda _cancel: iter(("updated",)),
            None,
            module_dir=Path("mod"),
            head=lambda _dest: heads.pop(0),
            channels=lambda: [soap],
            restart=refuse,
            pause=lambda _s: None,
        )
    )
    assert lines[-1] == (
        f"The restart was refused, so the server was not stopped: {REFUSED} The older bots log "
        f"in again at the next start. {botpool.AFTER_A_STOP}"
    )
