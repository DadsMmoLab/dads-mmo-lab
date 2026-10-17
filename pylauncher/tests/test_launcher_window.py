"""The client launcher window (T187): one server's game launcher, over its `ControllerView`.

Real widgets, offscreen, over the same docker-free fakes `test_controller_view`
uses -- a real ready-to-play client made by `play_client.create()` in
`tmp_path`, the real record, the real Play pipeline -- with only the database
reads (accounts, the online count, the realm row) answered by a fake, because
those need a server.

The window DRIVES the view: its PLAY, Make…, Refresh and Delete are the view's
own presses, so every refusal here is the view's guard speaking, not a copy.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import process_events
from tests.test_controller_view import (
    WORLD_UP,
    _Asked,
    _built,
    _client_entry,
    _game_client,
    _OptionsAsker,
    _play_view,
    _Ps,
    _this_host_owns,
)
from yulon import client_packs, play_client, runner
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.ui import controller_view as controller_view_module
from yulon.ui import launcher_window
from yulon.ui.controller_view import ControllerView
from yulon.ui.launcher_window import (
    ACCOUNT_ASK,
    ACCOUNT_KEEP,
    GAMES_OWN,
    LauncherWindow,
    Online,
    ServerReading,
    ViewReads,
    account_data,
)
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
UP = Online(bots=412, players=2, uptime=timedelta(hours=3, minutes=12))


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The view's own jobs run inline, as in `test_controller_view` (the window's are given)."""
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


@pytest.fixture
def boxes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every message box the VIEW puts up, by its text; none is shown (they are modal)."""
    said: list[str] = []
    box = controller_view_module.QMessageBox
    monkeypatch.setattr(box, "information", lambda *a, **_k: said.append(a[2]))
    monkeypatch.setattr(box, "warning", lambda *a, **_k: said.append(a[2]))
    monkeypatch.setattr(
        box, "question", lambda *a, **_k: said.append(a[2]) or box.StandardButton.No
    )
    return said


class _Reads(ViewReads):
    """The real client-folder reads; the database's answer is given, and every ask counted."""

    def __init__(self, view: ControllerView, answer: ServerReading) -> None:
        super().__init__(view)
        self.answer = answer
        self.asked = 0
        self.client_asked = 0

    def server(self) -> ServerReading:
        self.asked += 1
        return self.answer

    def client(self, play: Path) -> launcher_window.ClientReading:
        self.client_asked += 1
        return super().client(play)


SERVER = ServerReading(accounts=("ALICE", "BOB"), online=UP, announced="127.0.0.1")


def _launcher(
    ps: _Ps,
    tmp_path: Path,
    *,
    play: bool = True,
    entry: CatalogEntry = WOTLK,
    answer: ServerReading = SERVER,
    launcher: dict[str, Any] | None = None,
    asker: _Asked | None = None,
    options_asker: Any = None,
    job_runner: Any = run_inline,
    opened: list[Path] | None = None,
    addresses: tuple[str, ...] = (),
    view_runner: Any = None,
) -> tuple[LauncherWindow, ControllerView, Path | None]:
    """A launcher over a Server tab whose ready-to-play client is real (or not made yet)."""
    original = _game_client(tmp_path / "clients" / "WoW")
    play_dir = _built(original, tmp_path) if play else None
    if play_dir is not None and launcher is not None:
        _save(play_dir, launcher)
    view, _ = _play_view(
        ps,
        tmp_path,
        original=original,
        play=play_dir,
        entry=entry,
        asker=asker,
        options_asker=options_asker,
        job_runner=view_runner,
    )
    reads = _Reads(view, answer)
    sink: list[Path] = opened if opened is not None else []
    window = LauncherWindow(
        view,
        reads=reads,
        job_runner=job_runner,
        open_folder=lambda path: sink.append(path) is None,
        addresses=addresses,
    )
    return window, view, play_dir


def _save(play: Path, launcher: dict[str, Any]) -> None:
    record = client_packs.read_record(play)
    marker = play_client.read_marker(play)
    assert marker is not None
    client_packs.write_record(
        play,
        client_packs.PackRecord(
            record.packs, record.exe, record.choices, record.config_seeded, launcher
        ),
        game=marker.game,
        server_dir=Path(marker.server_dir),
    )


def _picks(play: Path | None) -> dict[str, Any]:
    assert play is not None
    return client_packs.read_record(play).launcher


def _choose(combo: Any, data: object) -> None:
    """Pick the item carrying `data` the way a person does: the `activated` signal."""
    index = combo.findData(data)
    assert (
        index >= 0
    ), f"{data!r} is not offered: {[combo.itemData(i) for i in range(combo.count())]}"
    combo.setCurrentIndex(index)
    combo.activated.emit(index)


def _type_address(window: LauncherWindow, text: str) -> None:
    """Type `text` into the realm box and press Return: what commits a typed address."""
    window.realm_combo.setEditText(text)
    window.realm_combo.lineEdit().returnPressed.emit()


def _online(view: ControllerView) -> None:
    """The Server tab's realm badge says what a poll that found the world up says."""
    view.realm_badge.set_status("running")


def _shown(window: LauncherWindow, size: tuple[int, int]) -> None:
    from yulon.ui.theme import apply_dadcraft_theme

    apply_dadcraft_theme(window)
    window.resize(*size)
    window.show()
    window.activateWindow()
    process_events()


@pytest.fixture
def closing() -> Iterator[list[LauncherWindow]]:
    """Windows to close after the test, so none outlives it on the offscreen screen."""
    made: list[LauncherWindow] = []
    yield made
    for window in made:
        window.close()
        window.deleteLater()
    process_events()


# -- the layout ------------------------------------------------------------------


def _top(widget: Any, window: LauncherWindow) -> int:
    from PySide6.QtCore import QPoint

    return widget.mapTo(window, QPoint(0, 0)).y()


def _left(widget: Any, window: LauncherWindow) -> int:
    from PySide6.QtCore import QPoint

    return widget.mapTo(window, QPoint(0, 0)).x()


def test_the_window_is_laid_out_as_the_approved_mockup(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    """Banner; left: Client, Realm address, then Log in as right above PLAY; right: the options."""
    window, _view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    _shown(window, (1280, 800))

    left = [window.client_box, window.realm_box, window.account_box, window.play_button]
    tops = [_top(w, window) for w in left]
    assert tops == sorted(tops), f"left column out of order: {tops}"
    assert all(_left(w, window) < _left(window.display_box, window) for w in left)
    right = [window.display_box, window.extras_box, window.addons_box]
    assert [_top(w, window) for w in right] == sorted(_top(w, window) for w in right)
    assert _top(window.banner, window) < min(tops)
    footer = _top(window.close_button, window)
    assert footer > _top(window.play_button, window)
    # PLAY at the bottom left: nothing of the left column under it but its own line.
    assert _top(window.play_button, window) > _top(window.account_box, window)
    assert window.play_button.text().strip() == "PLAY"
    assert window.play_button.property("primary") is True
    assert window.windowTitle() == f"Play — {WOTLK.name}"
    assert window.minimumWidth() == 960 and window.minimumHeight() == 640


def test_the_dpad_goes_realm_address_then_log_in_as_then_play_and_back(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    """Through the real `Navigator`, at the Steam Deck's size and at the smallest window."""
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction, Navigator

    window, _view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    nav = Navigator()
    for size in ((1280, 800), (960, 640), (1920, 1080)):
        _shown(window, size)
        window.realm_combo.setFocus()
        process_events()
        order = [QApplication.focusWidget()]
        for _ in range(2):
            nav.navigate(Direction.DOWN)
            order.append(QApplication.focusWidget())
        assert order == [window.realm_combo, window.account_combo, window.play_button], size
        for _ in range(2):
            nav.navigate(Direction.UP)
        assert QApplication.focusWidget() is window.realm_combo, size


def test_play_has_the_first_focus_and_tab_goes_realm_then_log_in_then_play(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, _view, _ = _launcher(ps, tmp_path)

    assert window.focusWidget() is window.play_button
    assert window.realm_combo.nextInFocusChain() is not None
    from PySide6.QtWidgets import QWidget

    chain: list[QWidget] = []
    widget: QWidget = window.realm_combo
    for _ in range(40):
        widget = widget.nextInFocusChain()
        if widget in (window.account_combo, window.play_button):
            chain.append(widget)
        if widget is window.play_button:
            break
    assert chain == [window.account_combo, window.play_button]


@pytest.mark.parametrize("size", [(960, 640), (1280, 800), (1920, 1080)])
def test_no_label_or_button_is_cut_off_from_the_smallest_window_up(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow], size: tuple[int, int]
) -> None:
    """Every button's text fits its width; every label's text fits its box; PLAY shows whole."""
    from PySide6.QtWidgets import QLabel, QPushButton

    entry = _client_entry(tmp_path, config=False)
    window, view, play = _launcher(ps, tmp_path, entry=entry)
    closing.append(window)
    _online(view)
    view._say_play("Downloading HD creatures… 40% (600.0 kB of 1.5 MB)")
    view._show_cancel(True)
    assert play is not None
    _shown(window, size)

    cut: list[str] = []
    for button in window.findChildren(QPushButton):
        if not button.isVisible():
            continue
        text = button.text()
        advance = button.fontMetrics().horizontalAdvance(text)
        chrome = max(0, button.sizeHint().width() - advance)
        if button.width() < advance + chrome:
            cut.append(f"button {text!r}: {button.width()} < {advance + chrome}")
    for label in window.findChildren(QLabel):
        if not label.isVisible() or not label.text():
            continue
        if label.wordWrap():
            needed = label.heightForWidth(label.width())
            if needed > label.height():
                cut.append(f"label {label.text()[:40]!r}: {label.height()}px < {needed}px")
        elif label.fontMetrics().horizontalAdvance(label.text()) > label.width():
            cut.append(f"label {label.text()[:40]!r}: wider than its {label.width()}px")
    assert cut == [], f"cut off at {size}: {cut}"
    # And nothing is out of sight: neither column needs its scrollbar, even with a
    # Play under way, so Log in as is never hidden under PLAY.
    assert window.left_scroll.verticalScrollBar().maximum() == 0, size
    assert window.right_scroll.verticalScrollBar().maximum() == 0, size
    from PySide6.QtCore import QPoint

    bottom = window.play_button.mapTo(window, QPoint(0, window.play_button.height())).y()
    assert bottom <= window.height(), "PLAY is off the window"
    assert window.play_button.height() >= 56


# -- the banner ------------------------------------------------------------------


def test_the_realm_pill_follows_the_server_tabs_badge(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, view, _ = _launcher(ps, tmp_path)
    assert window.realm_badge.status == view.realm_badge.status == "stopped"
    assert window.online_label.text() == "Play starts the server first (about 1 minute)"

    view.realm_badge.set_status("starting")
    assert window.realm_badge.status == "starting"

    _online(view)
    assert window.realm_badge.status == "running"
    assert window.online_label.text() == "412 bots and 2 players online · up 3h 12m"


def test_a_stopping_realm_does_not_say_play_starts_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T188 C5: the tab's Stop now holds the badge at "stopping"; the banner says so."""
    window, view, _ = _launcher(ps, tmp_path)
    _online(view)

    view.realm_badge.set_status("stopping")

    assert window.realm_badge.status == "stopping"
    assert window.online_label.text() == launcher_window.STOPPING_BANNER
    assert window.online_label.text() != launcher_window.STOPPED_BANNER
    # And the line under PLAY (final review): it said "The server is stopped:
    # PLAY starts it" while the banner above it said the server was stopping.
    assert window.play_reason_label.text() == launcher_window.STOPPING_REASON
    assert window.play_reason_label.text() != launcher_window.STOPPED_REASON


_REASONS = {
    "stopping": launcher_window.STOPPING_REASON,
    "starting": launcher_window.STARTING_REASON,
    "restarting": launcher_window.RESTARTING_REASON,
    "unknown": launcher_window.UNKNOWN_REASON,
    "partial": launcher_window.PARTIAL_REASON,
    "loop": launcher_window.LOOP_REASON,
}
"""The line under PLAY for each badge word that is neither REALM ONLINE nor OFFLINE."""


@pytest.mark.parametrize("state", list(_REASONS))
def test_the_line_under_play_says_what_the_badge_says(
    qapp: object, ps: _Ps, tmp_path: Path, state: str
) -> None:
    """Final review: every state but running fell through to "The server is stopped:
    PLAY starts it, waits for the realm, then starts the game." -- under STOPPING,
    STARTING and RESTARTING alike."""
    window, view, _ = _launcher(ps, tmp_path)
    _online(view)

    view.realm_badge.set_status(state)

    assert window.realm_badge.status == state
    said = window.play_reason_label.text()
    assert said == _REASONS[state], said
    assert not said.startswith("The server is stopped"), said


def test_a_crash_looping_realm_says_so_above_play(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """T391: the tab's badge says CRASH LOOP; the banner neither says starting nor offline."""
    window, view, _ = _launcher(ps, tmp_path)
    _online(view)

    view.realm_badge.set_status("loop")

    assert window.online_label.text() == launcher_window.LOOP_BANNER


def test_the_line_under_play_for_a_stopped_server_is_unchanged(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, view, _ = _launcher(ps, tmp_path)
    _online(view)

    view.realm_badge.set_status("stopped")

    assert window.play_reason_label.text() == launcher_window.STOPPED_REASON
    assert len(set(_REASONS.values()) | {launcher_window.STOPPED_REASON}) == len(_REASONS) + 1


def test_a_partly_up_server_is_not_called_stopped_under_play(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T188 fix round 1 (M3): the tab's badge says PARTLY UP; the line under PLAY agreed."""
    window, view, _ = _launcher(ps, tmp_path)
    ps.names = "ac-database\n"

    view.refresh_status()

    assert window.realm_badge.status == "partial"
    said = window.play_reason_label.text()
    assert "partly up" in said, said
    assert "stopped" not in said, said
    assert window.online_label.text() == launcher_window.PARTIAL_BANNER
    assert window.online_label.text() != launcher_window.STOPPED_BANNER


def test_a_realm_yulon_cannot_ask_about_is_not_called_stopped(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final fix round: Docker not answering says so; it neither claims stopped nor
    promises that PLAY starts the server."""
    from yulon import docker

    window, view, _ = _launcher(ps, tmp_path)

    def unreachable() -> object:
        raise docker.DockerCommandError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(view.services.controller, "status", unreachable)
    view.refresh_status()

    assert window.realm_badge.status == "unknown"
    # T194 C7: the launcher, the Server tab's banner and its badge say it one way.
    assert window.online_label.text() == view.docker_banner.title_label.text()
    assert window.online_label.text() == "Yu'lon can't ask Docker about this server right now"
    assert not view.docker_banner.isHidden()
    said = window.play_reason_label.text()
    assert "Docker" in said, said
    assert "stopped" not in said and "starts it" not in said, said


def test_a_count_that_could_not_be_read_is_left_out_not_shown_as_zero(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    answer = ServerReading(accounts=(), online=None, announced=None)
    window, view, _ = _launcher(ps, tmp_path, answer=answer)
    _online(view)

    assert window.online_label.text() == ""
    assert "0" not in window.online_label.text()


def test_the_server_is_read_when_the_realm_comes_up_and_not_while_it_is_down(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A stopped server's database would only time out; the reads wait for the realm."""
    window, view, _ = _launcher(ps, tmp_path)
    reads = window.reads
    assert isinstance(reads, _Reads)
    assert reads.asked == 0

    _online(view)
    assert reads.asked == 1


def test_every_read_goes_through_the_job_runner_never_the_gui_thread(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Nothing is read until the runner runs it; the default runner is the threaded one."""
    queued: list[tuple[Callable[[], object], Any, Any]] = []
    window, view, _ = _launcher(
        ps, tmp_path, job_runner=lambda work, done, failed: queued.append((work, done, failed))
    )
    reads = window.reads
    assert isinstance(reads, _Reads)
    _online(view)

    assert reads.asked == 0 and reads.client_asked == 0 and queued, "a read ran outside the runner"
    assert window.addons_list.count() == 0
    for work, done, _failed in list(queued):
        done(work())
    assert reads.asked == 1 and reads.client_asked == 1


def test_the_window_runs_its_reads_on_the_threaded_runner_unless_given_one(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`threaded_job_runner(window)`: the runner that keeps each worker referenced to its end."""
    made: list[object] = []

    def runner(parent: object) -> Any:
        made.append(parent)
        return lambda work, done, failed: None

    monkeypatch.setattr(launcher_window, "threaded_job_runner", runner)
    original = _game_client(tmp_path / "clients" / "WoW")
    view, _ = _play_view(ps, tmp_path, original=original, play=_built(original, tmp_path))

    window = LauncherWindow(view, reads=_Reads(view, SERVER))

    assert made == [window]


# -- PLAY ------------------------------------------------------------------------


def test_play_is_the_views_own_play_and_starts_the_game(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], boxes: list[str]
) -> None:
    window, view, play = _launcher(ps, tmp_path)
    ps.names = WORLD_UP

    window.play_button.click()

    assert len(launched) == 1, boxes
    assert window.progress_label.text() == view.play_label.text()
    assert "World of Warcraft was started at" in window.progress_label.text()
    assert play is not None
    assert not (play / "WTF" / "Config.wtf").exists(), "no picks, so nothing to write"


def test_the_line_after_play_stays_true_once_the_game_has_exited(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    launched: list[object],
    boxes: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T211 1: "World of Warcraft is starting…" was still on screen after the game exited.

    The game is started detached and Yu'lon never hears it end, so the line
    must not claim the game is doing anything now: it says when it was started.
    """
    from yulon.ui import controller_view as controller_view_module

    monkeypatch.setattr(controller_view_module, "_clock", lambda: "20:14")
    window, view, _play = _launcher(ps, tmp_path)
    ps.names = WORLD_UP

    window.play_button.click()

    assert len(launched) == 1, boxes
    for text in (view.play_label.text(), window.progress_label.text()):
        assert text.startswith("World of Warcraft was started at 20:14."), text
        assert "is starting" not in text


def test_the_launchers_picks_reach_config_wtf_on_a_shipped_game_with_no_client_data(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], boxes: list[str]
) -> None:
    """WotLK's real entry has no packs, exe patch or `config_wtf`: the picks must still apply.

    Before T187's window, Play went straight to the launch on such an entry and
    the Display and Log in as choices would have been saved and never written.
    """
    window, view, play = _launcher(ps, tmp_path)
    assert play is not None
    _online(view)
    _choose(window.window_combo, "windowed")
    _choose(window.resolution_combo, "1600x900")
    _choose(window.account_combo, account_data("BOB"))
    ps.names = WORLD_UP

    window.play_button.click()

    config = (play / "WTF" / "Config.wtf").read_text(encoding="utf-8")
    assert 'SET gxWindow "1"' in config and 'SET gxMaximize "0"' in config
    assert 'SET gxResolution "1600x900"' in config and 'SET accountName "BOB"' in config
    assert len(launched) == 1, boxes


def test_without_a_ready_to_play_client_play_is_greyed_and_says_to_make_one(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[str]
) -> None:
    asker = _Asked(cancel=True)
    window, _view, _ = _launcher(ps, tmp_path, play=False, asker=asker)

    assert not window.play_button.isEnabled()
    assert "Make a ready-to-play client" in window.play_reason_label.text()
    assert window.client_box.isHidden() and not window.make_box.isHidden()
    assert window.focusWidget() is window.make_button
    for settings in (window.display_box, window.account_box, window.realm_box):
        assert not settings.isEnabled()

    window.make_button.click()
    assert len(asker.offers) == 1, "Make… is the view's own Make…"


def test_progress_and_cancel_are_the_views_own(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    window, view, _ = _launcher(ps, tmp_path)
    assert window.progress_label.isHidden() and window.cancel_button.isHidden()

    view._say_play("Downloading HD creatures… 40%")
    view._show_cancel(True)
    assert window.progress_label.text() == "Downloading HD creatures… 40%"
    assert not window.progress_label.isHidden() and not window.cancel_button.isHidden()

    window.cancel_button.click()
    assert view._play_cancel.is_set(), "Cancel is the view's Cancel"
    assert window.cancel_button.text() == "Cancelling…" and not window.cancel_button.isEnabled()

    view._show_cancel(False)
    assert window.cancel_button.isHidden()


# -- Review Focus 3: the view's guards refuse while it is busy -------------------


def test_play_refresh_and_delete_are_refused_through_the_views_guard_during_a_module_job(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], boxes: list[str]
) -> None:
    window, view, play = _launcher(ps, tmp_path)
    assert play is not None
    ps.names = WORLD_UP
    view._module_pending = "install mod-transmog"

    for button in (window.play_button, window.refresh_button, window.delete_button):
        boxes.clear()
        button.click()
        assert len(boxes) == 1 and "“install mod-transmog” is running" in boxes[0], button.text()

    assert launched == [] and play.is_dir()


def test_make_is_refused_through_the_views_guard_while_the_server_is_busy(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[str]
) -> None:
    asker = _Asked(cancel=True)
    window, view, _ = _launcher(ps, tmp_path, play=False, asker=asker)
    view._set_busy(True)

    window.make_button.click()

    assert asker.offers == []
    assert boxes and "busy with another action" in boxes[0]


@pytest.mark.parametrize("hold", ["busy", "make", "play"])
def test_the_settings_wait_while_the_view_works_on_the_client(
    qapp: object, ps: _Ps, tmp_path: Path, hold: str
) -> None:
    """Greyed while a job could write the record, so no save can be lost under it."""
    window, view, play = _launcher(ps, tmp_path)
    settings = (window.display_box, window.account_box, window.realm_box, window.extras_box)
    assert all(box.isEnabled() for box in settings)

    if hold == "busy":
        view._set_busy(True)
    elif hold == "make":
        view._play_client_running = True
        view._say_play("Looking at your client…")
    else:
        view._play_pending = True
        view._say_play("Checking that the server is running…")
    assert not any(box.isEnabled() for box in settings)
    # And a save that got past the greying is refused by the view, not lost.
    assert view.save_launcher_picks(launcher={"account": "BOB"}) is not None
    assert "account" not in _picks(play)

    if hold == "busy":
        view._set_busy(False)
    elif hold == "make":
        view._release_play_client()
    else:
        view._play_end()
    assert all(box.isEnabled() for box in settings)


# -- the settings, each saved into the record ------------------------------------


def test_each_display_choice_is_saved_and_nothing_else_is_written(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, _view, play = _launcher(ps, tmp_path)
    assert play is not None
    before = sorted(p.name for p in play.iterdir() if p.name != client_packs.RECORD)

    _choose(window.window_combo, "fullscreen")
    assert _picks(play) == {"display": {"window": "fullscreen"}}
    _choose(window.resolution_combo, "1280x800")
    assert _picks(play) == {"display": {"window": "fullscreen", "resolution": "1280x800"}}
    _choose(window.window_combo, "maximized")
    assert _picks(play)["display"]["window"] == "maximized"
    _choose(window.resolution_combo, "")
    assert _picks(play) == {"display": {"window": "maximized"}}

    after = sorted(p.name for p in play.iterdir() if p.name != client_packs.RECORD)
    assert after == before, "a change wrote into the client"
    assert not (play / "WTF").exists(), "Config.wtf is Play's to write, never a change's"


def test_borderless_is_offered_only_where_the_exe_patch_has_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    plain, _view, _ = _launcher(ps, tmp_path / "plain")
    assert plain.window_combo.findData("borderless") < 0

    window, _view2, _ = _launcher(ps, tmp_path / "patched", entry=_client_entry(tmp_path))
    assert window.window_combo.findData("borderless") >= 0


def test_the_window_pick_and_client_options_borderless_stay_one_choice(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Borderless picked here is the exe option; unticked in Client options, the pick follows."""
    entry = _client_entry(tmp_path)
    off = {"packs": {"hd": False}, "exe_options": {"borderless": False}}
    window, view, play = _launcher(ps, tmp_path, entry=entry, options_asker=_OptionsAsker(off))
    assert play is not None

    _choose(window.window_combo, "borderless")
    assert client_packs.read_record(play).choices["exe_options"]["borderless"] is True

    view.client_options()
    assert _picks(play)["display"]["window"] == "windowed"
    assert window.window_combo.currentData() == "windowed", "the window did not re-read it"


def test_log_in_as_offers_the_servers_accounts_and_ask_in_the_game(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, view, play = _launcher(ps, tmp_path)
    _online(view)
    offered = [window.account_combo.itemText(i) for i in range(window.account_combo.count())]
    assert "ALICE" in offered and "BOB" in offered and "Ask in the game" in offered

    _choose(window.account_combo, account_data("BOB"))
    assert _picks(play) == {"account": "BOB"}
    _choose(window.account_combo, ACCOUNT_ASK)
    assert _picks(play) == {"account": None}
    assert "password is never stored" in window.account_note.text()


def test_a_typed_realm_address_is_saved_and_use_this_computer_takes_it_out(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, _view, play = _launcher(ps, tmp_path)
    assert window.realm_combo.currentText() == "127.0.0.1"
    assert window.realm_combo.isEditable()

    _type_address(window, "  192.168.1.20 ")
    assert _picks(play) == {"realm_address": "192.168.1.20"}

    window.this_computer_button.click()
    assert _picks(play) == {"realm_address": None}, "said, so Play takes the old lines out"
    assert window.realm_combo.currentText() == "127.0.0.1"
    offered = [window.realm_combo.itemText(i) for i in range(window.realm_combo.count())]
    assert "192.168.1.20" in offered, "an address typed earlier is offered again"


def test_the_default_address_is_never_saved_as_a_typed_one(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Lead ruling: realm_address only when the player typed one; this computer is the default."""
    window, _view, play = _launcher(ps, tmp_path, launcher={"realm_address": "10.0.0.5"})
    assert window.realm_combo.currentText() == "10.0.0.5"

    _type_address(window, "127.0.0.1")

    assert _picks(play) == {"realm_address": None}, "typing 127.0.0.1 is Use this computer"

    fresh, _view2, fresh_play = _launcher(ps, tmp_path / "fresh")
    _type_address(fresh, "127.0.0.1")
    assert _picks(fresh_play) == {}, "nothing typed over, nothing to take out"


def test_the_focus_leaving_a_greyed_realm_box_is_not_a_new_address(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Greying the box as a save starts moves the focus out of it: no second save, no note."""
    window, view, play = _launcher(ps, tmp_path)
    view._set_busy(True)

    _type_address(window, "10.0.0.5")

    assert window.settings_note.isHidden() and window.realm_note.isHidden()
    assert _picks(play) == {}


def test_an_address_the_game_cannot_use_is_refused_with_why(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, _view, play = _launcher(ps, tmp_path)

    _type_address(window, "my server; rm -rf")

    assert _picks(play) == {}
    assert "not an address" in window.realm_note.text() and not window.realm_note.isHidden()
    assert window.realm_combo.currentText() == "127.0.0.1"


def test_extras_are_the_client_options_packs_with_their_sizes(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    entry = _client_entry(tmp_path)
    window, _view, play = _launcher(ps, tmp_path, entry=entry)
    assert play is not None
    assert list(window.pack_boxes) == ["hd"], "only the optional packs are choices"
    assert window.pack_boxes["hd"].text() == "HD creatures"
    assert window.pack_sizes["hd"].text() == "1.5 MB"
    assert not window.pack_boxes["hd"].isChecked()

    window.pack_boxes["hd"].click()

    assert client_packs.read_record(play).choices["packs"] == {"hd": True}


def test_a_server_with_no_extras_says_so(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    window, _view, _ = _launcher(ps, tmp_path)
    assert window.pack_boxes == {}
    assert "no extras" in window.extras_note.text()


def test_the_addons_in_the_client_are_listed_and_their_folder_opens(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    original = _game_client(tmp_path / "clients" / "WoW")
    play = _built(original, tmp_path)
    for name in ("Questie-335", "Blizzard_AuctionUI", "AtlasLoot"):
        (play / "Interface" / "AddOns" / name).mkdir(parents=True)
    opened: list[Path] = []
    view, _ = _play_view(ps, tmp_path, original=original, play=play)
    window = LauncherWindow(
        view,
        reads=_Reads(view, SERVER),
        job_runner=run_inline,
        open_folder=lambda path: opened.append(path) is None,
    )

    shown = [window.addons_list.item(i).text() for i in range(window.addons_list.count())]
    assert shown == ["AtlasLoot", "Questie-335"]
    window.open_addons_button.click()
    window.open_folder_button.click()
    assert opened[-2:] == [play / "Interface" / "AddOns", play]


# -- Review Focus 1: the realm address the server announces ----------------------


def test_an_address_other_than_the_one_the_realm_announces_is_hinted_and_play_stays(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    answer = ServerReading(accounts=(), online=UP, announced="192.168.1.20")
    window, view, _ = _launcher(ps, tmp_path, answer=answer)
    assert window.realm_mismatch_label.isHidden(), "hinted before the realm row was read"

    _online(view)
    hint = window.realm_mismatch_label.text()
    assert not window.realm_mismatch_label.isHidden()
    assert "192.168.1.20" in hint and "Networking tab" in hint
    assert window.play_button.isEnabled()

    _type_address(window, "192.168.1.20")
    assert window.realm_mismatch_label.isHidden()


def test_no_hint_when_the_realm_row_could_not_be_read(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    answer = ServerReading(accounts=(), online=UP, announced=None)
    window, view, _ = _launcher(ps, tmp_path, answer=answer)
    _online(view)
    _type_address(window, "10.0.0.5")

    assert window.realm_mismatch_label.isHidden()


# -- T667: a realm address that is another computer's -----------------------------


def test_a_remote_address_does_not_promise_that_play_starts_the_local_server(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _this_host_owns(monkeypatch)
    window, view, _ = _launcher(ps, tmp_path, launcher={"realm_address": "192.168.0.60"})
    view.realm_badge.set_status("stopped")

    banner = window.online_label.text()
    reason = window.play_reason_label.text()
    assert "192.168.0.60" in banner and "192.168.0.60" in reason
    # Someone hosting for friends types their own public address: say where the server starts.
    assert "start it on the Server tab first" in reason
    assert banner != launcher_window.STOPPED_BANNER
    assert reason != launcher_window.STOPPED_REASON
    for text in (banner, reason):
        assert "starts the server" not in text and "starts it" not in text, text
        assert "waits for the realm" not in text, text
    assert window.realm_badge.isHidden(), "the local server's badge under a remote login"
    assert window.play_button.isEnabled()


def test_a_remote_address_is_not_told_the_local_server_sends_it_on(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local realm row (here 127.0.0.1) says nothing about the server at the typed address."""
    _this_host_owns(monkeypatch)
    window, view, _ = _launcher(ps, tmp_path, launcher={"realm_address": "192.168.0.60"})
    _online(view)

    assert window.realm_mismatch_label.isHidden()
    assert "PLAY still works" not in window.realm_mismatch_label.text()


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.0.60"])
def test_this_computers_own_address_keeps_the_start_and_wait_promise(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    _this_host_owns(monkeypatch, "192.168.0.60")
    window, view, _ = _launcher(ps, tmp_path, launcher={"realm_address": address})
    view.realm_badge.set_status("stopped")

    assert window.online_label.text() == launcher_window.STOPPED_BANNER
    assert window.play_reason_label.text() == launcher_window.STOPPED_REASON
    assert not window.realm_badge.isHidden()


def test_typing_another_computers_address_changes_the_banner_and_back_again(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _this_host_owns(monkeypatch)
    window, view, _ = _launcher(ps, tmp_path)
    view.realm_badge.set_status("stopped")
    assert window.online_label.text() == launcher_window.STOPPED_BANNER

    _type_address(window, "10.0.0.5")
    assert "10.0.0.5" in window.online_label.text()

    _type_address(window, "127.0.0.1")
    assert window.online_label.text() == launcher_window.STOPPED_BANNER
    assert not window.realm_badge.isHidden()


# -- Review Focus 2: a saved account the server no longer has --------------------


def test_a_saved_account_no_longer_on_the_server_falls_back_to_ask_with_a_note(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    answer = ServerReading(accounts=("BOB",), online=UP, announced=None)
    window, view, play = _launcher(ps, tmp_path, answer=answer, launcher={"account": "ALICE"})
    assert window.account_combo.currentData() == account_data("ALICE"), "kept until it is read"

    _online(view)

    assert window.account_combo.currentData() == ACCOUNT_ASK
    assert "ALICE" in window.account_note.text() and "no longer" in window.account_note.text()
    assert _picks(play) == {"account": None}, "the stale name would be written at Play"


def test_a_saved_account_is_kept_while_the_accounts_cannot_be_read(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    answer = ServerReading(accounts=None, online=None, announced=None)
    window, view, play = _launcher(ps, tmp_path, answer=answer, launcher={"account": "ALICE"})

    _online(view)

    assert window.account_combo.currentData() == account_data("ALICE")
    assert "no longer" not in window.account_note.text()
    assert _picks(play) == {"account": "ALICE"}


def test_a_saved_account_is_matched_whatever_case_the_database_gives(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    answer = ServerReading(accounts=("alice",), online=UP, announced=None)
    window, view, play = _launcher(ps, tmp_path, answer=answer, launcher={"account": "ALICE"})

    _online(view)

    assert window.account_combo.currentData() == account_data("ALICE")
    assert _picks(play) == {"account": "ALICE"}


# -- Review Focus 5: a key the catalog sets for every player ---------------------


def test_a_key_the_catalog_forces_is_shown_fixed_by_the_server_and_cannot_be_changed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    entry = _client_entry(tmp_path)
    assert entry.client.config_wtf is not None
    cfg = entry.client.config_wtf.model_copy(
        update={
            "always": {"gxWindow": "0", "gxResolution": "1024x768", "accountName": "GM"},
            "seed": {},
        }
    )
    entry = entry.model_copy(update={"client": entry.client.model_copy(update={"config_wtf": cfg})})
    window, view, _ = _launcher(ps, tmp_path, entry=entry)
    _online(view)

    for combo, shown in (
        (window.window_combo, "Full screen"),
        (window.resolution_combo, "1024 × 768"),
        (window.account_combo, "GM"),
    ):
        assert not combo.isEnabled()
        assert shown in combo.currentText() and "set by this server" in combo.currentText()


# -- the client box --------------------------------------------------------------


def test_the_client_box_names_both_folders_and_says_it_is_up_to_date(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, _view, play = _launcher(ps, tmp_path)
    assert play is not None

    assert window.client_path_label.toolTip() == str(play)
    assert window.client_source_label.toolTip() == str(tmp_path / "clients" / "WoW")
    assert "never changed" in window.client_source_note.text()
    assert "Up to date" in window.client_status_label.text()


def test_a_patched_original_says_refresh(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    original = _game_client(tmp_path / "clients" / "WoW")
    play = _built(original, tmp_path)
    (original / "Wow.exe").write_bytes(b"MZ the game, patched")
    view, _ = _play_view(ps, tmp_path, original=original, play=play)

    window = LauncherWindow(view, reads=_Reads(view, SERVER), job_runner=run_inline)

    assert "Refresh" in window.client_status_label.text()
    assert "Wow.exe" in window.client_status_label.toolTip()


# -- the footer, and a view that goes away ---------------------------------------


def test_the_footer_has_the_build_server_tab_and_close(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, _view, _ = _launcher(ps, tmp_path)
    asked: list[tuple[str, object]] = []
    window.server_tab_requested.connect(lambda game, server: asked.append((game, server)))

    assert "12340" in window.build_label.text()
    window.server_tab_button.click()
    assert asked == [(WOTLK.id, tmp_path)]


def test_a_view_that_is_gone_is_never_called_into(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Task 4 closes the window with its tab; until then nothing may reach a dead view."""
    import shiboken6

    window, view, _ = _launcher(ps, tmp_path)
    shiboken6.delete(view)
    process_events()

    assert not window.play_button.isEnabled()
    window.play_button.click()  # a queued press: nothing happens, nothing raises
    assert "server's tab was closed" in window.play_reason_label.text()


def test_set_view_points_the_window_at_the_rebuilt_tab(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """After Make…/Delete `main.py` rebuilds the tab; the window follows the new view."""
    window, _old, _ = _launcher(ps, tmp_path, play=False)
    assert not window.play_button.isEnabled()
    original = tmp_path / "clients" / "WoW"
    play = _built(original, tmp_path)
    new, _ = _play_view(ps, tmp_path, original=original, play=play)

    window.set_view(new)

    assert window.play_button.isEnabled() and not window.client_box.isHidden()
    assert window.client_path_label.toolTip() == str(play)
    assert launcher_window.LauncherWindow is LauncherWindow


# -- fix round 1 --------------------------------------------------------------------


def test_a_stale_account_waits_for_the_view_to_be_idle_before_it_is_saved(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """I1: no save, and no refusal note, while a Play holds the record; saved once it ends."""
    answer = ServerReading(accounts=("BOB",), online=UP, announced=None)
    window, view, play = _launcher(ps, tmp_path, answer=answer, launcher={"account": "ALICE"})
    view._play_pending = True

    _online(view)

    assert window.settings_note.isHidden(), window.settings_note.text()
    assert _picks(play) == {"account": "ALICE"}

    view._play_end()

    assert _picks(play) == {"account": None}
    assert "ALICE" in window.account_note.text()


def test_the_no_pick_choices_are_named_for_what_they_leave_alone(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Lead ruling: Log in as says "As the game remembers it"; Window can always go back."""
    window, view, play = _launcher(ps, tmp_path, launcher={"display": {"window": "windowed"}})
    _online(view)

    keep = window.account_combo.findData(ACCOUNT_KEEP)
    assert window.account_combo.itemText(keep) == "As the game remembers it"
    assert window.window_combo.findData("") >= 0, "a saved mode could not be cleared"
    assert window.window_combo.itemText(window.window_combo.findData("")) == GAMES_OWN

    _choose(window.window_combo, "")
    assert _picks(play) == {}


def test_clearing_a_borderless_pick_also_clears_the_borderless_exe_option(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, _view, play = _launcher(ps, tmp_path, entry=_client_entry(tmp_path))
    assert play is not None
    _choose(window.window_combo, "borderless")
    assert client_packs.read_record(play).choices["exe_options"]["borderless"] is True

    _choose(window.window_combo, "")

    assert "display" not in _picks(play)
    assert client_packs.read_record(play).choices["exe_options"]["borderless"] is False
    assert window.window_combo.currentData() == "", "it snapped back to Borderless"


def test_clearing_borderless_ticked_in_client_options_clears_the_exe_option_too(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """No window pick, Borderless ticked in Client options: the window shows it, and clears it."""
    on = {"packs": {}, "exe_options": {"borderless": True}}
    window, view, play = _launcher(
        ps, tmp_path, entry=_client_entry(tmp_path), options_asker=_OptionsAsker(on)
    )
    assert play is not None
    view.client_options()
    assert window.window_combo.currentData() == "borderless"

    _choose(window.window_combo, "")

    assert client_packs.read_record(play).choices["exe_options"]["borderless"] is False
    assert window.window_combo.currentData() == ""


def _play_once(window: LauncherWindow, ps: _Ps) -> None:
    ps.names = WORLD_UP
    window.play_button.click()


def test_use_this_computer_takes_the_typed_address_back_out_of_config_wtf(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], boxes: list[str]
) -> None:
    """Lead ruling (M5), an entry without `config_wtf`: realmlist.wtf carries this computer."""
    window, _view, play = _launcher(ps, tmp_path)
    assert play is not None
    _type_address(window, "10.0.0.5")
    _play_once(window, ps)
    config = play / "WTF" / "Config.wtf"
    assert 'SET realmList "10.0.0.5"' in config.read_text(encoding="utf-8")

    window.this_computer_button.click()
    _play_once(window, ps)

    text = config.read_text(encoding="utf-8")
    assert "realmlist" not in text.casefold() and "patchlist" not in text.casefold(), text
    realmlist = (play / "Data" / "enUS" / "realmlist.wtf").read_text(encoding="utf-8")
    assert realmlist.startswith("set realmlist 127.0.0.1\n")
    assert len(launched) == 2, boxes


def test_use_this_computer_keeps_the_catalogs_own_realmlist_in_config_wtf(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], boxes: list[str]
) -> None:
    """An entry WITH `config_wtf`: its `always` realmList is written again, never taken out."""
    entry = _client_entry(tmp_path, with_packs=False, exe=False)
    window, _view, play = _launcher(ps, tmp_path, entry=entry)
    assert play is not None
    _type_address(window, "10.0.0.5")
    _play_once(window, ps)
    config = play / "WTF" / "Config.wtf"
    assert 'SET realmList "10.0.0.5"' in config.read_text(encoding="utf-8")

    window.this_computer_button.click()
    _play_once(window, ps)

    text = config.read_text(encoding="utf-8")
    assert 'SET realmList "127.0.0.1"' in text and "10.0.0.5" not in text, text
    assert len(launched) == 2, boxes


def test_typing_in_the_realm_box_survives_a_re_render(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    """M2: the realm coming up (a new read, a re-render) does not reset what is being typed."""
    answer = ServerReading(accounts=(), online=UP, announced="192.168.1.20")
    window, view, _ = _launcher(ps, tmp_path, answer=answer)
    closing.append(window)
    _shown(window, (1280, 800))
    window.realm_combo.setFocus()
    process_events()
    window.realm_combo.setEditText("192.168.1.2")

    _online(view)

    assert window.realm_combo.currentText() == "192.168.1.2"
    assert not window.realm_mismatch_label.isHidden(), "the hint still follows the read"


def test_the_addons_list_takes_the_right_columns_spare_height(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    original = _game_client(tmp_path / "clients" / "WoW")
    play = _built(original, tmp_path)
    for n in range(12):
        (play / "Interface" / "AddOns" / f"Addon{n:02d}").mkdir(parents=True)
    view, _ = _play_view(ps, tmp_path, original=original, play=play)
    window = LauncherWindow(view, reads=_Reads(view, SERVER), job_runner=run_inline)
    closing.append(window)

    heights = {}
    for size in ((1280, 800), (1920, 1080)):
        _shown(window, size)
        heights[size] = window.addons_list.height()
    row = window.addons_list.sizeHintForRow(0)
    assert heights[(1280, 800)] >= 3 * row, heights
    assert heights[(1920, 1080)] > heights[(1280, 800)] + 200, heights
    assert window.right_scroll.verticalScrollBar().maximum() == 0


# -- Task 4: what Task 3's reviews left to the window's opening -----------------------


class _HeldRunner:
    """A job runner that keeps each job until `run_all()`: a read still on its worker."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Callable[[], object], Any, Any]] = []

    def __call__(self, work: Callable[[], object], done: Any, failed: Any) -> None:
        self.jobs.append((work, done, failed))

    def run_all(self) -> None:
        while self.jobs:
            work, done, failed = self.jobs.pop(0)
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - handed on, as the real runner does
                failed(exc)
            else:
                done(result)


def test_an_owed_account_check_waits_for_the_fresh_reading_and_never_saves_over_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """4a: ALICE was stale and owed while a Play held the record; the record now says BOB.

    When the Play ends the client is read again. The owed check must wait for that
    reading: run on the old one it saw ALICE, and wrote "Ask" over a valid BOB.
    """
    held = _HeldRunner()
    answer = ServerReading(accounts=("BOB",), online=UP, announced=None)
    window, view, play = _launcher(
        ps, tmp_path, answer=answer, launcher={"account": "ALICE"}, job_runner=held
    )
    assert play is not None
    held.run_all()
    view._play_pending = True
    view._say_play("Starting the game…")  # the window sees the view busy
    _online(view)
    held.run_all()
    assert _picks(play) == {"account": "ALICE"}, "saved while the Play held the record"

    _save(play, {"account": "BOB"})  # what the record says by the time the Play ends
    view._play_end()
    assert _picks(play) == {"account": "BOB"}, "the owed check ran on the stale reading"
    held.run_all()

    assert _picks(play) == {"account": "BOB"}
    assert window.account_combo.currentData() == account_data("BOB")


def test_an_owed_account_check_still_runs_once_the_fresh_reading_lands(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The other half of 4a: still stale after the reload, it is saved as Ask then."""
    held = _HeldRunner()
    answer = ServerReading(accounts=("BOB",), online=UP, announced=None)
    window, view, play = _launcher(
        ps, tmp_path, answer=answer, launcher={"account": "ALICE"}, job_runner=held
    )
    held.run_all()
    view._play_pending = True
    view._say_play("Starting the game…")
    _online(view)
    held.run_all()

    view._play_end()
    held.run_all()

    assert _picks(play) == {"account": None}
    assert "ALICE" in window.account_note.text()


def test_the_online_count_is_read_again_while_the_window_shows_the_realm_up(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    """4b: a timer, reading on the runner, only while shown and only while the realm is up."""
    window, view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    reads = window.reads
    assert isinstance(reads, _Reads)
    timer = window.online_timer
    assert timer.interval() == launcher_window.ONLINE_REFRESH_MS == 60_000

    _shown(window, (1280, 800))
    assert not timer.isActive(), "running while the realm is down"
    _online(view)
    assert timer.isActive()
    assert "2 players" in window.online_label.text() and "up 3h 12m" in window.online_label.text()
    names = [window.account_combo.itemText(i) for i in range(window.account_combo.count())]
    asked = reads.asked

    reads.answer = ServerReading(
        accounts=("ALICE", "BOB", "CAROL"),
        online=Online(bots=400, players=5, uptime=timedelta(hours=4, minutes=1)),
        announced="127.0.0.1",
    )
    timer.timeout.emit()

    assert reads.asked == asked + 1, "the refresh did not go through the runner"
    assert window.online_label.text() == "400 bots and 5 players online · up 4h 1m"
    after = [window.account_combo.itemText(i) for i in range(window.account_combo.count())]
    assert after == names, "a refresh of the count rebuilt the account box under the player"

    window.hide()
    process_events()
    assert not timer.isActive(), "still reading for a window nobody sees"
    window.show()
    process_events()
    assert timer.isActive()
    view.realm_badge.set_status("stopped")
    assert not timer.isActive()


def test_a_refresh_landing_after_the_realm_went_down_changes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    """A count read for a realm that has since stopped is dropped, not kept for the next start."""
    held = _HeldRunner()
    window, view, _ = _launcher(ps, tmp_path, job_runner=held)
    closing.append(window)
    reads = window.reads
    assert isinstance(reads, _Reads)
    _shown(window, (1280, 800))
    _online(view)
    held.run_all()
    reads.answer = ServerReading(accounts=("ALICE", "BOB"), online=Online(9, 9), announced=None)
    window.online_timer.timeout.emit()
    view.realm_badge.set_status("stopped")
    held.run_all()

    assert window.online_label.text() == launcher_window.STOPPED_BANNER
    view.realm_badge.set_status("running")  # the banner shows what it holds until the read lands
    assert window.online_label.text().startswith("412 bots and 2 players online")


def test_the_views_dialogs_from_a_launcher_press_open_over_the_launcher(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    closing: list[LauncherWindow],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4c: a refusal for PLAY pressed in the launcher is the launcher's, not the main window's."""
    window, view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    _shown(window, (1280, 800))
    parents: list[object] = []
    box = controller_view_module.QMessageBox
    monkeypatch.setattr(box, "information", lambda parent, *_a, **_k: parents.append(parent))
    view._module_pending = "Install Transmog"

    window.play_button.click()
    window.refresh_button.click()
    window.delete_button.click()
    assert parents == [window, window, window]

    # The same refusals from the Server tab's own ▾ menu open over the tab.
    view.refresh_play_client_action.trigger()
    view.delete_play_client_action.trigger()
    assert parents[3:] == [view, view]


def test_start_the_server_is_asked_over_the_launcher(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    closing: list[LauncherWindow],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    _shown(window, (1280, 800))
    asked: list[tuple[object, str]] = []
    monkeypatch.setattr(
        controller_view_module,
        "_ask_with",
        lambda parent, title, *_a, **_k: asked.append((parent, title)),
    )

    window.play_button.click()

    assert asked == [(window, "Start the server?")]


def test_a_closed_launcher_does_not_take_the_tabs_dialogs(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, view, _ = _launcher(ps, tmp_path)
    _shown(window, (1280, 800))
    parents: list[object] = []
    box = controller_view_module.QMessageBox
    monkeypatch.setattr(box, "information", lambda parent, *_a, **_k: parents.append(parent))
    view._module_pending = "Install Transmog"
    window.play_button.click()
    window.close()

    view.play()

    assert parents == [window, view]


def test_typed_addresses_are_offered_again_and_handed_over_newest_first(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """4d: the window offers the remembered history and says what to remember; main.py keeps it."""
    window, _view, _ = _launcher(ps, tmp_path, addresses=("old.lan", "me@pw", "127.0.0.1"))
    offered = [window.realm_combo.itemText(i) for i in range(window.realm_combo.count())]
    assert "old.lan" in offered
    assert "me@pw" not in offered, "a refused address came back from the history"
    told: list[list[str]] = []
    window.addresses_changed.connect(told.append)

    _type_address(window, "10.0.0.5")
    assert told == [["10.0.0.5", "old.lan"]]
    _type_address(window, "bad address!")
    assert len(told) == 1, "a refused address was handed over to be remembered"
    _type_address(window, "old.lan")
    assert told[-1] == ["old.lan", "10.0.0.5"]
    for n in range(6):
        _type_address(window, f"host{n}.lan")
    assert told[-1] == ["host5.lan", "host4.lan", "host3.lan", "host2.lan", "host1.lan"]


def test_b_on_the_pad_closes_the_launcher_and_the_dpad_walks_it(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    """4e: the app's one navigator drives the launcher as the active window; B closes it."""
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Action, Direction, Navigator

    window, _view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    nav = Navigator()
    _shown(window, (1280, 800))
    assert QApplication.activeWindow() is window
    assert QApplication.focusWidget() is window.play_button, "PLAY is not the first stop"

    nav.navigate(Direction.UP)
    assert QApplication.focusWidget() is window.account_combo
    nav.navigate(Direction.UP)
    assert QApplication.focusWidget() is window.realm_combo

    assert nav.perform(Action.BACK)
    assert window.isHidden()


def test_b_closes_an_open_dropdown_before_the_launcher(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    from yulon.ui.gamepad import Action, Navigator

    window, _view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    nav = Navigator()
    _shown(window, (1280, 800))
    window.account_combo.showPopup()
    process_events()

    assert nav.perform(Action.BACK)
    process_events()

    assert not window.isHidden(), "B closed the window under an open dropdown"


def test_the_window_opens_at_its_size_clamped_to_the_screen() -> None:
    """3: about 1100 x 760, never past the screen and never under the 960 x 640 minimum."""
    from PySide6.QtCore import QSize

    assert launcher_window.fitted_size(QSize(1920, 1040)) == QSize(1100, 760)
    assert launcher_window.fitted_size(QSize(1280, 760)) == QSize(1100, 760)
    assert launcher_window.fitted_size(QSize(1024, 700)) == QSize(1024, 700)
    assert launcher_window.fitted_size(QSize(800, 560)) == QSize(960, 640)


def test_the_geometry_round_trips_as_text_and_rubbish_is_refused(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    from PySide6.QtCore import QSize

    window, _view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    _shown(window, (1000, 700))
    text = window.geometry_text()
    assert isinstance(text, str) and text

    other, _v, _ = _launcher(ps, tmp_path / "other")
    closing.append(other)
    assert other.restore_geometry_text(text)
    # The height as saved. The width is kept on the screen by Qt, and the
    # offscreen screen is 800 wide, so 1000 comes back as the 960 minimum
    # (measured: a plain QWidget does the same).
    assert other.size() == QSize(960, 700)
    assert window.size() == QSize(1000, 700)
    assert not other.restore_geometry_text("not base64 at all!")
    assert not other.restore_geometry_text("")


def test_closing_the_launcher_says_so(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    window, _view, _ = _launcher(ps, tmp_path)
    said: list[int] = []
    window.closed.connect(lambda: said.append(1))
    _shown(window, (1280, 800))

    window.close()

    assert said == [1] and window.isHidden()


# -- Task 4, fix round 1 --------------------------------------------------------------


def _owed_alice_then_bob(
    ps: _Ps, tmp_path: Path
) -> tuple[_HeldRunner, LauncherWindow, ControllerView, Path]:
    """ALICE saved and stale (owed while a Play holds the record); the record now says BOB."""
    held = _HeldRunner()
    answer = ServerReading(accounts=("BOB",), online=UP, announced=None)
    window, view, play = _launcher(
        ps, tmp_path, answer=answer, launcher={"account": "ALICE"}, job_runner=held
    )
    assert play is not None
    held.run_all()
    view._play_pending = True
    view._say_play("Starting the game…")
    _online(view)
    held.run_all()
    assert _picks(play) == {"account": "ALICE"}
    _save(play, {"account": "BOB"})
    return held, window, view, play


@pytest.mark.parametrize(
    "end",
    [
        pytest.param(lambda view: view._play_end(), id="no-text"),
        pytest.param(lambda view: view._play_end("Nothing was started."), id="nothing-started"),
        pytest.param(lambda view: view._play_launched(None), id="launched"),
        pytest.param(
            lambda view: (view._cancel_play_download(), view._play_end("Cancelled.")),
            id="cancel",
        ),
    ],
)
def test_an_owed_account_check_never_runs_while_a_client_read_is_on_its_way(
    qapp: object, ps: _Ps, tmp_path: Path, end: Callable[[ControllerView], object]
) -> None:
    """Fix 1: `_play_end(said)` tells the window twice; neither may judge the stale reading."""
    held, window, view, play = _owed_alice_then_bob(ps, tmp_path)

    end(view)
    assert _picks(play) == {"account": "BOB"}, "the owed check ran on the stale reading"
    held.run_all()
    view.play_state_changed.emit()  # any later word from the view: nothing is owed now

    assert _picks(play) == {"account": "BOB"}
    assert window.account_combo.currentData() == account_data("BOB")


def test_reopening_a_hidden_launcher_reads_the_client_and_server_again(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    """Fix 2: closed, the player adds an addon and an account; opened again, both show."""
    window, view, play = _launcher(ps, tmp_path)
    assert play is not None
    closing.append(window)
    _shown(window, (1280, 800))
    _online(view)
    window.close()
    process_events()

    (play / "Interface" / "AddOns" / "Questie").mkdir(parents=True)
    reads = window.reads
    assert isinstance(reads, _Reads)
    reads.answer = ServerReading(accounts=("ALICE", "BOB", "CAROL"), online=UP, announced=None)
    window.show()
    process_events()

    addons = [window.addons_list.item(i).text() for i in range(window.addons_list.count())]
    assert "Questie" in addons
    assert window.account_combo.findData(account_data("CAROL")) >= 0


def test_a_launcher_shown_the_first_time_reads_once(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    window, _view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    reads = window.reads
    assert isinstance(reads, _Reads)
    asked = reads.client_asked
    _shown(window, (1280, 800))
    assert reads.client_asked == asked, "the first show read the client a second time"


def test_a_minimized_launcher_does_not_take_the_tabs_dialogs(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    closing: list[LauncherWindow],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, view, _ = _launcher(ps, tmp_path)
    closing.append(window)
    _shown(window, (1280, 800))
    parents: list[object] = []
    box = controller_view_module.QMessageBox
    monkeypatch.setattr(box, "information", lambda parent, *_a, **_k: parents.append(parent))
    view._module_pending = "Install Transmog"
    window.play_button.click()
    window.showMinimized()
    process_events()

    view.play()

    assert parents == [window, view]


# -- Task 5: what Task 4's review left ----------------------------------------------


def test_an_owed_account_check_after_a_failed_client_read_waits_for_a_good_one(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    """The read after the Play fails: the reading held is still the ALICE one, too old to judge."""
    held, window, view, play = _owed_alice_then_bob(ps, tmp_path)
    closing.append(window)
    view._play_end()
    assert window._client_pending

    def unreadable(_play: Path) -> launcher_window.ClientReading:
        raise OSError("client unreadable")

    reads = window.reads
    assert isinstance(reads, _Reads)
    good = reads.client
    setattr(reads, "client", unreadable)  # noqa: B010 - a seam swapped mid-test
    held.run_all()
    assert not window._client_pending
    view.play_state_changed.emit()  # the view says "idle" again: still nothing to judge by

    assert _picks(play) == {"account": "BOB"}, "the owed check judged the reading the failure left"

    setattr(reads, "client", good)  # noqa: B010 - put back
    window.show()
    window.close()
    window.show()  # opened again: read again, and this time it works
    held.run_all()
    assert _picks(play) == {"account": "BOB"}
    assert window.account_combo.currentData() == account_data("BOB")


def test_a_server_read_landing_before_the_client_read_does_not_judge_the_old_reading(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    held, window, view, play = _owed_alice_then_bob(ps, tmp_path)
    view._play_end()
    assert window._client_pending
    view.realm_badge.set_status("stopped")
    view.realm_badge.set_status("running")  # the server read again while the client read is out
    server = [job for job in held.jobs if job[1] == window._server_read]
    held.jobs = [job for job in held.jobs if job[1] != window._server_read]
    assert server

    for work, done, _failed in server:
        done(work())
    assert _picks(play) == {"account": "BOB"}, "the server read judged the stale client reading"
    held.run_all()

    assert _picks(play) == {"account": "BOB"}


def test_reopening_the_launcher_while_a_client_read_is_out_stays_consistent(
    qapp: object, ps: _Ps, tmp_path: Path, closing: list[LauncherWindow]
) -> None:
    held, window, view, play = _owed_alice_then_bob(ps, tmp_path)
    closing.append(window)
    view._play_end()
    window.show()
    window.close()
    window.show()

    held.run_all()

    assert not window._client_pending
    assert _picks(play) == {"account": "BOB"}


def test_reads_asked_for_the_old_view_never_land_after_set_view(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Make…/Delete rebuilt the tab while a read was on its worker: its answer is the old view's."""
    held = _HeldRunner()
    window, old, play = _launcher(ps, tmp_path, launcher={"account": "ALICE"}, job_runner=held)
    assert play is not None
    _online(old)
    assert any(job[1] == window._client_read for job in held.jobs)
    assert any(job[1] == window._server_read for job in held.jobs)
    new, _ = _play_view(ps, tmp_path, original=tmp_path / "clients" / "WoW", play=None)

    window.set_view(new)
    held.run_all()

    assert window._client is None and window._server is None
    assert not window._client_pending, "the dropped answer left the window waiting for ever"


# -- Final fix round (whole-branch reviews) ------------------------------------------


def test_a_failed_stale_account_save_is_tried_once_and_warned_once(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """A read-only record: the "Ask" save fails, and nothing tries it again in a loop.

    The failure ends the save, which tells the window the view is idle, which reads
    the client again, which finds ALICE still saved: that must not save again.
    """
    answer = ServerReading(accounts=("BOB",), online=UP, announced=None)
    window, view, play = _launcher(ps, tmp_path, answer=answer, launcher={"account": "ALICE"})
    assert play is not None
    writes: list[int] = []
    real = client_packs.write_record

    def read_only(*_a: object, **_k: object) -> None:
        writes.append(1)
        if len(writes) > 20:
            raise KeyboardInterrupt  # the loop: stop it here rather than hang the suite
        raise client_packs.PackError("The record is read-only.")

    monkeypatch.setattr(client_packs, "write_record", read_only)
    with caplog.at_level("WARNING", logger="yulon.ui.launcher_window"):
        _online(view)
        view.play_state_changed.emit()  # any later word from the view
        window._reload_client()  # and another reading of the same record

    assert len(writes) == 1, f"{len(writes)} saves from one stale account"
    warned = [r for r in caplog.records if r.name == "yulon.ui.launcher_window"]
    assert len(warned) == 1, [r.getMessage() for r in warned]
    assert _picks(play) == {"account": "ALICE"}

    monkeypatch.setattr(client_packs, "write_record", real)
    _save(play, {"account": "ALICE", "display": {"window": "windowed"}})  # the record changed
    window._reload_client()

    assert _picks(play) == {"account": None, "display": {"window": "windowed"}}


def test_set_view_lets_a_failed_stale_account_save_be_tried_again(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = ServerReading(accounts=("BOB",), online=UP, announced=None)
    window, view, play = _launcher(ps, tmp_path, answer=answer, launcher={"account": "ALICE"})
    assert play is not None
    real = client_packs.write_record

    def read_only(*_a: object, **_k: object) -> None:
        raise client_packs.PackError("The record is read-only.")

    monkeypatch.setattr(client_packs, "write_record", read_only)
    _online(view)
    monkeypatch.setattr(client_packs, "write_record", real)

    window.set_view(view)

    assert _picks(play) == {"account": None}


def test_play_saves_a_typed_address_first_and_then_plays_in_one_press(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed, not committed, PLAY pressed: the save runs, and Play starts when it is done."""
    held = _HeldRunner()
    window, view, play = _launcher(ps, tmp_path, view_runner=held)
    assert play is not None
    played: list[dict[str, Any]] = []
    monkeypatch.setattr(view, "play", lambda: played.append(_picks(play)))
    window.realm_combo.setEditText("10.0.0.7")

    window.play_button.click()

    assert played == [], "Play started before the address was saved"
    assert not window.play_button.isEnabled()
    held.run_all()
    assert played == [{"realm_address": "10.0.0.7"}]
    assert window.play_button.isEnabled()


def test_play_is_greyed_with_why_while_a_launcher_save_runs(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return committed the address; a PLAY pressed before the save lands is not refused."""
    held = _HeldRunner()
    window, view, play = _launcher(ps, tmp_path, view_runner=held)
    assert play is not None
    played: list[int] = []
    monkeypatch.setattr(view, "play", lambda: played.append(1))

    _type_address(window, "10.0.0.7")

    assert not window.play_button.isEnabled()
    assert "Saving" in window.play_reason_label.text()
    window.play_button.click()
    assert played == []
    held.run_all()
    assert window.play_button.isEnabled()
    assert "Saving" not in window.play_reason_label.text()
    assert _picks(play) == {"realm_address": "10.0.0.7"}
    window.play_button.click()
    assert played == [1]


def test_play_with_a_typed_address_the_game_cannot_use_says_why_and_does_not_play(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, view, play = _launcher(ps, tmp_path)
    played: list[int] = []
    monkeypatch.setattr(view, "play", lambda: played.append(1))
    window.realm_combo.setEditText("10.0.")

    window.play_button.click()

    assert played == [] and _picks(play) == {}
    assert "not an address" in window.realm_note.text()


def test_use_this_computer_writes_this_computers_address_where_config_wtf_is_the_only_one(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], boxes: list[str]
) -> None:
    """Codex: no realmlist.wtf (removed) and no catalog realmList: removing the lines left
    the typed address in place, so this computer's address is written instead."""
    entry = _client_entry(tmp_path, with_packs=False, exe=False, remove_locale=True)
    config_wtf = entry.client.config_wtf
    assert config_wtf is not None
    config_wtf = config_wtf.model_copy(update={"always": {}})
    entry = entry.model_copy(
        update={"client": entry.client.model_copy(update={"config_wtf": config_wtf})}
    )
    window, _view, play = _launcher(ps, tmp_path, entry=entry)
    assert play is not None
    _type_address(window, "10.0.0.5")
    _play_once(window, ps)
    config = play / "WTF" / "Config.wtf"
    assert 'SET realmList "10.0.0.5"' in config.read_text(encoding="utf-8")

    window.this_computer_button.click()
    _play_once(window, ps)

    text = config.read_text(encoding="utf-8")
    assert 'SET realmList "127.0.0.1"' in text and 'SET patchList "127.0.0.1"' in text, text
    assert "10.0.0.5" not in text, text
    assert len(launched) == 2, boxes


@pytest.mark.parametrize("spelling", [("interface", "addons"), ("INTERFACE", "AddOns")])
def test_open_addons_folder_finds_it_whatever_its_case(
    qapp: object, ps: _Ps, tmp_path: Path, spelling: tuple[str, str]
) -> None:
    opened: list[Path] = []
    window, _view, play = _launcher(ps, tmp_path, play=True, opened=opened)
    assert play is not None
    import shutil

    shutil.rmtree(play / "Interface", ignore_errors=True)
    (play / spelling[0] / spelling[1] / "Questie").mkdir(parents=True)
    window._reload_client()

    window.open_addons_button.click()

    assert opened == [play / spelling[0] / spelling[1]]


@pytest.mark.parametrize("half", ["10.0.", ".example.com", ".", "-", ":", "..", "-.:"])
def test_a_half_typed_address_is_neither_saved_nor_remembered(
    qapp: object, ps: _Ps, tmp_path: Path, half: str
) -> None:
    window, _view, play = _launcher(ps, tmp_path)
    remembered: list[list[str]] = []
    window.addresses_changed.connect(remembered.append)

    _type_address(window, half)

    assert _picks(play) == {}, half
    assert remembered == []
    assert "not an address" in window.realm_note.text()


def test_the_focus_leaving_the_realm_box_saves_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Half typed and the focus moves on: Return, a pick from the list, or PLAY commits it."""
    window, _view, play = _launcher(ps, tmp_path)
    window.realm_combo.setEditText("10.0")

    window.realm_combo.lineEdit().editingFinished.emit()

    assert _picks(play) == {}


def test_as_the_game_remembers_it_is_offered_after_a_name_is_picked(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    window, view, play = _launcher(ps, tmp_path)
    _online(view)
    _choose(window.account_combo, account_data("BOB"))
    assert _picks(play) == {"account": "BOB"}

    _choose(window.account_combo, ACCOUNT_KEEP)

    assert _picks(play) == {}
    assert window.account_combo.currentData() == ACCOUNT_KEEP
    assert window.account_combo.currentText() == "As the game remembers it"


def test_play_on_a_stopped_server_whose_start_is_refused_starts_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T179 final round: the launcher's PLAY reaches the controller's one start door.

    A world update left unfinished refuses every start there (`start_guard`); the
    launcher shows the view's sentence and neither the server nor the game starts.
    """
    refused = (
        "This server's last update didn't finish importing its world tables. Press "
        "“Finish the world update” first."
    )
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "exec",
        lambda self: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    window, view, _play = _launcher(ps, tmp_path)
    view.services.controller.start_guard = lambda: refused
    ps.names = ""

    window.play_button.click()

    assert launched == []
    assert not any(c[:3] == ["docker", "compose", "up"] for c in ps.calls)
    assert view.problem_label.text() == refused
    assert window.progress_label.text() == controller_view_module.PLAY_START_FAILED
