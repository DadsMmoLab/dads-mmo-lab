"""T786: "Put the folders back…" in the Modules tab's "Server build ▾" menu.

T766 built the engine press (`StagedInstaller.put_the_folders_back()`) and
`native.put_the_folders_back_offered()`; T783's Rebuild refusal sends the player to this entry.
The entry is shown and enabled exactly when the engine says the press is offered, runs the
engine's press as a job in the shared build panel (the engine holds the server's reservation,
the view takes none), and is read again after every job and on the tab's first draw.

The engine is the real one over a real git folder (`test_sources_off_put_back`); only the
reservation and the world's state are doubles, as there.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import process_events, wait_for_panel
from tests.support_native import Recorder
from tests.test_controller_view import WOTLK, _Ps, _services
from tests.test_rebuild_start_records import a_finished_install
from tests.test_sources_off_put_back import (  # noqa: F401 - _no_docker is an autouse fixture
    _no_docker,
    _off_core,
    _press_engine,
    _running,
)
from yulon import install_wiring, runner, server_build_presses
from yulon.catalog import native
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

LABEL = server_build_presses.PUT_THE_FOLDERS_BACK
_REAL_RUN = runner.run
"""Taken at import, before any fixture replaces it: the press's git runs for real."""


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    """`test_controller_view.ps`, except that git (never docker) reaches the real runner.

    The put-back is a checkout in a real repository (`test_sources_off_put_back`), and the
    shared double answers every command with an empty success, so the folder would not move.
    """
    fake = _Ps()

    def run(cmd: list[str], *a: Any, **kw: Any) -> Any:
        if cmd[0] == "git":
            return _REAL_RUN(cmd, *a, **kw)
        return fake(cmd, kw.get("cwd"), kw.get("timeout"))

    monkeypatch.setattr(runner, "run", run)
    return fake


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tab's reads run inline, so no test leaves a job whose answer it never took (T771)."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _say_yes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer every yes/no question Yes; return the texts asked."""
    asked: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> object:
        asked.append(text)
        return controller_view_module.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    return asked


def _view(
    ps: _Ps,
    server_dir: Path,
    rec: Recorder,
    *,
    claim: Any = None,
    world: Any = None,
) -> ControllerView:
    """The Modules tab over `server_dir`, its press wired through the real route and engine."""
    kwargs: dict[str, Any] = {}
    if claim is not None:
        kwargs["claim"] = claim
    if world is not None:
        kwargs["world"] = world
    services = _services(ps, server_dir.parent, [])
    services.put_the_folders_back = install_wiring.put_the_folders_back_for_app(
        WOTLK, server_dir, engine=lambda: _press_engine(rec, **kwargs)
    )
    return ControllerView(WOTLK, services, status_poll_ms=0)


def test_the_entry_is_in_the_server_version_section_after_the_other_presses(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    view = _view(ps, server_dir, rec)

    assert view.server_version_section.isAncestorOf(view.put_the_folders_back_button)
    assert view.put_the_folders_back_button.text() == "Put the folders back…"
    assert view.server_version_section.title() == "Server version"


def test_the_entry_is_hidden_while_the_engine_does_not_offer_the_press(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    assert native.put_the_folders_back_offered(server_dir) is False
    view = _view(ps, server_dir, rec)

    assert not view.put_the_folders_back_action.isVisible()
    assert not view.put_the_folders_back_action.isEnabled()


def test_the_entry_is_hidden_on_a_tab_with_no_route(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)

    assert not view.put_the_folders_back_action.isVisible()


def test_the_entry_is_shown_while_a_folder_is_off_its_build(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    view = _view(ps, server_dir, rec)

    assert view.put_the_folders_back_action.isVisible()
    assert view.put_the_folders_back_action.isEnabled()
    assert view.put_the_folders_back_button.isEnabled()


def test_the_entry_is_shown_while_the_write_back_marker_is_there(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    assert native.owe_write_back(server_dir) == ""
    view = _view(ps, server_dir, rec)

    assert view.put_the_folders_back_action.isVisible()
    assert view.put_the_folders_back_action.isEnabled()


def test_the_entry_is_not_offered_while_a_distro_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading a stopped distro's folder would boot it: not asked, so not offered."""
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    route = install_wiring.put_the_folders_back_for_app(
        WOTLK, server_dir, engine=lambda: _press_engine(rec)
    )
    assert route is not None and route.offered() is True
    monkeypatch.setattr(install_wiring, "_in_the_distro", lambda *_a: True)
    monkeypatch.setattr(install_wiring, "_distro_down", lambda *_a: True)
    down = install_wiring.put_the_folders_back_for_app(
        WOTLK, server_dir, wsl_distro="Ubuntu", engine=lambda: _press_engine(rec)
    )
    assert down is not None and down.offered() is False


def test_the_question_says_what_the_press_does_and_that_the_server_must_be_stopped() -> None:
    said = native.put_the_folders_back_confirmation(WOTLK, Path("/srv/wow"))

    assert "/srv/wow" in said
    assert "stopped" in said
    assert "compose" in said and "patch" in said
    assert "Say no and nothing happens" in said
    assert "database" in said, "it says the databases are not touched"


def test_pressing_it_asks_then_runs_the_engine_once_under_one_reservation(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, built, stray = _off_core(tmp_path)
    reserved: list[tuple[Path, str]] = []

    @contextmanager
    def recording(folder: Path, *, press: str, **_kw: Any) -> Iterator[None]:
        reserved.append((Path(folder), press))
        yield None

    asked = _say_yes(monkeypatch)
    view = _view(ps, server_dir, rec, claim=recording)
    assert native.read_head_file(server_dir) == stray

    assert view.put_the_folders_back_action.isEnabled()
    view.put_the_folders_back_action.trigger()
    wait_for_panel(view.rebuild_log)
    process_events()

    assert len(asked) == 1 and "stopped" in asked[0]
    assert reserved == [(server_dir, LABEL)], "the engine's one claim, and the view takes none"
    assert native.read_head_file(server_dir) == built
    assert f"was put back on {built[:7]}" in view.rebuild_log.text()
    assert native.sources_still_off(server_dir) == ()
    # The menu is read again after the job: the press worked, so the entry is gone.
    assert not view.put_the_folders_back_action.isVisible()
    assert not view.put_the_folders_back_action.isEnabled()


def test_declining_the_question_moves_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, _built, stray = _off_core(tmp_path)
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "question",
        lambda *a, **k: controller_view_module.QMessageBox.StandardButton.No,
    )
    view = _view(ps, server_dir, rec)

    assert view.put_the_folders_back() is False

    assert not view.rebuild_log.running
    assert native.read_head_file(server_dir) == stray
    assert view.put_the_folders_back_action.isVisible()


def test_a_running_world_is_the_engines_refusal_in_the_panel_and_nothing_moves(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, built, stray = _off_core(tmp_path)
    _say_yes(monkeypatch)
    failures: list[str] = []
    view = _view(ps, server_dir, rec, world=_running)
    view.action_failed.connect(failures.append)

    view.put_the_folders_back_action.trigger()
    wait_for_panel(view.rebuild_log)
    process_events()

    text = view.rebuild_log.status_text()
    assert "FAILED" in text and "is running" in text, text
    assert "Press Stop on the Server tab" in text, text
    assert failures and "Nothing was put back." in failures[0], failures
    assert native.read_head_file(server_dir) == stray
    assert {row.built for row in native.sources_still_off(server_dir)} == {built}
    assert view.put_the_folders_back_action.isVisible(), "still owed, so still offered"
    assert view.put_the_folders_back_action.isEnabled()


def test_a_job_that_is_running_greys_the_entry_and_its_finish_gives_it_back(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    view = _view(ps, server_dir, rec)

    view._set_busy(True, "a test job")
    assert view.put_the_folders_back_action.isVisible()
    assert not view.put_the_folders_back_action.isEnabled()
    view._set_busy(False)
    assert view.put_the_folders_back_action.isEnabled()


def test_a_server_build_press_that_finishes_reads_the_entry_again(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuild (here a stand-in that clears the owed marker) ends: the entry goes."""
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    assert native.owe_write_back(server_dir) == ""
    _say_yes(monkeypatch)
    view = _view(ps, server_dir, rec)
    assert view.put_the_folders_back_action.isVisible()

    def rebuild(cancel: object = None) -> Iterator[str]:
        native.forget_write_back_owed(server_dir)
        yield "rebuilt"

    view.services.rebuild = rebuild
    view.services.rebuild_refusal = None
    assert view.rebuild_server() is True
    wait_for_panel(view.rebuild_log)
    process_events()

    assert not view.put_the_folders_back_action.isVisible()


def test_a_press_while_another_job_runs_is_refused_before_the_question(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, _built, stray = _off_core(tmp_path)
    asked = _say_yes(monkeypatch)
    monkeypatch.setattr(
        controller_view_module,
        "show_information",
        lambda *a, **k: None,
    )
    view = _view(ps, server_dir, rec)
    view._set_busy(True, "a test job")

    assert view.put_the_folders_back() is False

    assert asked == []
    assert native.read_head_file(server_dir) == stray
