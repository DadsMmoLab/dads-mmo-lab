"""T622 item 2: the Tuning tab's saves and reverts run as jobs, so the window never waits on Docker.

`save_tuning`, `save_tuning_file`, `revert_tuning` and `revert_tuning_file` write under the
server's cross-process hold (T610). Taking it costs a Docker round trip or three, and on a slow
daemon much more, so it must not happen on the thread that paints. Each press now checks what it
can from memory, then hands the hold and the write to the view's job runner; what the player
reads (the report, the refusals, the banner) is the same, and is shown when the job lands.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.test_controller_view import _Ps
from tests.test_more_writes_hold import HELD, TUNING_FILE, _conf, _Hold
from yulon.ui import controller_view as cv


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    from yulon import runner
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(cv, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


Job = tuple[Callable[[], object], Callable[[object], None], Callable[[object], None]]


def _queued_view(ps: _Ps, tmp_path: Path, hold: Any) -> tuple[cv.ControllerView, list[Job]]:
    """A Tuning view whose job runner only queues: what the press did itself is what it did."""
    from tests.test_controller_view import WOTLK, _services
    from yulon import server_time_zone

    conf = tmp_path / TUNING_FILE
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("BeastMaster.Enable = 1\n", encoding="utf-8")
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-npc-beastmaster"})}
    )
    object.__setattr__(services, "time_zone", server_time_zone.time_zone_route(WOTLK, tmp_path))
    object.__setattr__(services, "hold_server", hold)
    queued: list[Job] = []
    view = cv.ControllerView(
        WOTLK,
        services,
        status_poll_ms=0,
        job_runner=lambda work, done, failed: queued.append((work, done, failed)),
    )
    queued.clear()
    return view, queued


def _writes(queued: list[Job]) -> list[Job]:
    return [job for job in queued if getattr(job[1], "__func__", None) is _WRITE_DONE]


_WRITE_DONE = cv.ControllerView._tuning_write_done


def _land(job: Job, *, on: str = "worker") -> None:
    """Run the job's work (on another thread, as the runner does) and deliver its answer."""
    work, done, failed = job
    box: list[object] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            box.append(work())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(10)
    assert not worker.is_alive()
    if errors:
        failed(errors[0])
    else:
        done(box[0])


class _Slow(_Hold):
    """A hold that takes `seconds` to be made, and records the thread that made it."""

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self.seconds = seconds
        self.threads: list[int] = []

    def __call__(self, press: str, budget: float | None = None) -> Any:
        self.threads.append(threading.get_ident())
        time.sleep(self.seconds)
        return super().__call__(press, budget)


def _card_edit(view: cv.ControllerView) -> Any:
    card = view.tuning_panel.card("mod-npc-beastmaster")
    card.editors["BeastMaster.Enable"].control.setChecked(False)
    return card


PRESSES = ["card_save", "file_save", "card_revert", "file_revert"]


def _press(view: cv.ControllerView, which: str) -> None:
    if which == "card_save":
        card = _card_edit(view)
        card.save_button.click()
    elif which == "file_save":
        view.open_tuning_file(TUNING_FILE)
        view.save_tuning_file("BeastMaster.Enable = 0\n")
    elif which == "card_revert":
        card = view.tuning_panel.card("mod-npc-beastmaster")
        view.revert_tuning(card.card.family, card.card.module_id)
    else:
        view.open_tuning_file(TUNING_FILE)
        view.revert_tuning_file()


def _a_backup_exists(view: cv.ControllerView, tmp_path: Path) -> None:
    """Give the two reverts something to put back, by saving once through the inline path."""
    from yulon import tuning

    tuning.backup(tmp_path / TUNING_FILE, root=tmp_path)


@pytest.mark.parametrize("which", PRESSES)
def test_a_press_takes_no_hold_and_writes_nothing_on_the_calling_thread(
    qapp: object, ps: _Ps, tmp_path: Path, which: str
) -> None:
    """Mutation: do the hold and the write inline in the slot, as before T622."""
    hold = _Slow(0.0)
    view, queued = _queued_view(ps, tmp_path, hold)
    _a_backup_exists(view, tmp_path)
    before = _conf(tmp_path)
    began = time.monotonic()
    _press(view, which)
    assert hold.threads == [], "the hold was taken on the thread that paints"
    assert _conf(tmp_path) == before
    assert len(_writes(queued)) == 1, "one job, handed to the runner"
    assert time.monotonic() - began < 2


@pytest.mark.parametrize("which", PRESSES)
def test_the_job_takes_the_hold_on_its_own_thread_and_the_answer_is_shown_when_it_lands(
    qapp: object, ps: _Ps, tmp_path: Path, which: str
) -> None:
    events: list[str] = []
    hold = _Slow(0.0)
    hold.events = events
    view, queued = _queued_view(ps, tmp_path, hold)
    _a_backup_exists(view, tmp_path)
    _press(view, which)
    _land(_writes(queued)[0])
    assert hold.threads and hold.threads[0] != threading.get_ident()
    assert events[0].startswith("hold:") and events[-1] == "release"
    if "save" in which:
        assert _conf(tmp_path) == "BeastMaster.Enable = 0\n"
    assert view.tuning_report.toPlainText() != ""


@pytest.mark.parametrize("which", PRESSES)
def test_a_held_server_is_refused_with_the_sentence_and_nothing_written_when_the_job_lands(
    qapp: object, ps: _Ps, tmp_path: Path, which: str
) -> None:
    view, queued = _queued_view(ps, tmp_path, _Hold(refuse=True))
    _a_backup_exists(view, tmp_path)
    before = _conf(tmp_path)
    failed: list[str] = []
    view.action_failed.connect(failed.append)
    _press(view, which)
    _land(_writes(queued)[0])
    assert _conf(tmp_path) == before
    assert HELD in view.tuning_report.toPlainText()
    assert failed == [HELD]
    assert not list(tmp_path.glob("**/*.bak.new"))


def test_a_slow_hold_does_not_hold_the_window_up(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """A 3 s take: the press returns at once and only the job waits. Mutation: inline write."""
    hold = _Slow(3.0)
    view, queued = _queued_view(ps, tmp_path, hold)
    began = time.monotonic()
    _press(view, "card_save")
    assert time.monotonic() - began < 1.0
    assert hold.threads == []


@pytest.mark.parametrize("which", PRESSES)
def test_a_second_press_while_a_write_is_pending_is_refused_in_words(
    qapp: object, ps: _Ps, tmp_path: Path, which: str
) -> None:
    """Two writes in flight would race each other's backup and file. Mutation: no flag."""
    view, queued = _queued_view(ps, tmp_path, _Hold())
    _a_backup_exists(view, tmp_path)
    _press(view, "card_save")
    assert len(_writes(queued)) == 1
    _press(view, which)
    assert len(_writes(queued)) == 1, "a second job was queued behind the first"
    assert view.tuning_report.toPlainText() == cv.TUNING_WRITE_RUNNING
    _land(_writes(queued)[0])
    assert _conf(tmp_path) == "BeastMaster.Enable = 0\n"
    assert view.tuning_report.toPlainText() != cv.TUNING_WRITE_RUNNING
    _press(view, "file_save")
    assert len(_writes(queued)) == 2, "once it has landed the next press is taken"


def test_a_reload_asked_while_a_write_is_pending_waits_for_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The cards must not be redrawn from a file that is half written. Mutation: no deferral."""
    view, queued = _queued_view(ps, tmp_path, _Hold())
    _press(view, "card_save")
    before = len(queued)
    view.reload_tuning()
    assert len(queued) == before, "the tab was read while the write was in flight"
    _land(_writes(queued)[0])
    card = view.tuning_panel.card("mod-npc-beastmaster")
    assert card.editors["BeastMaster.Enable"].control.isChecked() is False
    assert not card.edits(), "the redraw read the saved file, not the typed one"


def test_a_job_that_breaks_says_so_and_frees_the_tab(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view, queued = _queued_view(ps, tmp_path, _Hold())
    _press(view, "card_save")
    work, done, failed = _writes(queued)[0]
    failed(RuntimeError("disk on fire"))
    assert "disk on fire" in view.tuning_details.text()
    assert view.tuning_report.toPlainText() == cv.TUNING_WRITE_BROKE
    _press(view, "file_save")
    assert len(_writes(queued)) == 2, "the tab is free again"


def test_the_reverts_mark_a_put_back_as_running_until_the_job_lands(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """`run_restore` refuses while a put-back runs; now that it is a job the flag has to span it."""
    view, queued = _queued_view(ps, tmp_path, _Hold())
    _a_backup_exists(view, tmp_path)
    _press(view, "file_revert")
    assert view._put_back_running is True
    _land(_writes(queued)[0])
    assert view._put_back_running is False


def test_a_cards_revert_that_lands_later_still_redraws_that_card_from_the_file(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T190: a pressed card's typing is not carried across the redraw its press causes.

    The redraw now comes when the job lands, long after the click returned. Mutation: do not
    `defer_pressed_card()` when the job is queued, and the typing comes back as if no Revert
    had been pressed.
    """
    view, queued = _queued_view(ps, tmp_path, _Hold())
    _a_backup_exists(view, tmp_path)
    card = _card_edit(view)  # typed: switched off; the file and its backup say on
    assert card.edits()
    assert card.revert_button is not None
    card.revert_button.click()
    _land(_writes(queued)[0])
    card = view.tuning_panel.card("mod-npc-beastmaster")
    assert card.editors["BeastMaster.Enable"].control.isChecked() is True
    assert not card.edits()


def test_a_refused_save_keeps_the_typing_through_the_next_redraw(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Before T622 a refusal kept the edits. Mutation: owe the card when the job is queued."""
    view, queued = _queued_view(ps, tmp_path, _Hold(refuse=True))
    card = _card_edit(view)
    card.save_button.click()
    _land(_writes(queued)[0])
    assert HELD in view.tuning_report.toPlainText()
    assert view.tuning_panel.card("mod-npc-beastmaster").edits(), "right after the refusal"
    view.reload_tuning()  # any later redraw: Reload, another card's save
    assert view.tuning_panel.card("mod-npc-beastmaster").edits(), "dropped by the next redraw"


def test_a_save_that_fails_to_write_keeps_the_typing_through_the_next_redraw(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import tuning

    def fail(*_a: Any, **_k: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(tuning, "write", fail)
    view, queued = _queued_view(ps, tmp_path, _Hold())
    card = _card_edit(view)
    card.save_button.click()
    _land(_writes(queued)[0])
    assert "disk full" in view.tuning_report.toPlainText()
    view.reload_tuning()
    assert view.tuning_panel.card("mod-npc-beastmaster").edits()


# ---- the other presses that write the same files wait for a pending save (and the reverse)


def _pending_save(ps: _Ps, tmp_path: Path) -> tuple[cv.ControllerView, list[Job]]:
    view, queued = _queued_view(ps, tmp_path, _Hold())
    object.__setattr__(view.services, "reset_settings", lambda *_a: None)
    view._set_reset_button()
    _a_backup_exists(view, tmp_path)
    _press(view, "card_save")
    assert view._tuning_writing and len(_writes(queued)) == 1
    return view, queued


def _reset_all(view: cv.ControllerView) -> None:
    from tests.test_controller_view import TUNING_RESET_ALL, _menu_action

    _menu_action(view, TUNING_RESET_ALL).trigger()


def _undo(view: cv.ControllerView) -> None:
    view._undo_items = (object(),)  # type: ignore[assignment]
    view.undo_last_reset()


def _bot_count(view: cv.ControllerView) -> None:
    view.apply_bot_count()


def _time_zone(view: cv.ControllerView) -> None:
    view.apply_time_zone()


def _repair_files(view: cv.ControllerView) -> None:
    view.repair_server_files()


def _text_of(view: cv.ControllerView, which: str) -> str:
    return {
        "tuning": view.tuning_report.toPlainText,
        "bots": view.bot_count_report.text,
        "problem": view.problem_label.text,
    }[which]()


@pytest.mark.parametrize(
    ("press", "where"),
    [
        (_reset_all, "tuning"),
        (_undo, "tuning"),
        (_repair_files, "problem"),
    ],
    ids=["reset", "undo", "repair-files"],
)
def test_a_press_that_writes_the_same_files_waits_for_a_pending_save(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, press: Any, where: str
) -> None:
    """Mutation: drop the `_tuning_writing` refusal from that press."""
    from tests.test_controller_view import _reset_yes

    view, queued = _pending_save(ps, tmp_path)
    _reset_yes(monkeypatch)
    jobs = len(queued)
    press(view)
    assert _text_of(view, where) == cv.TUNING_WRITE_RUNNING or (
        cv.TUNING_WRITE_RUNNING in _text_of(view, where)
    )
    assert len(queued) == jobs, "a second writer was handed to the runner"


def test_the_bot_count_and_the_time_zone_wait_for_a_pending_save(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_controller_view import _reset_yes

    view, queued = _pending_save(ps, tmp_path)
    _reset_yes(monkeypatch)
    # Armed as a landed reading leaves them: the refusal must come from the pending save.
    object.__setattr__(view.services, "bot_population", object())
    view._bot_count_reading = type("R", (), {"problem": None})()  # type: ignore[assignment]
    jobs = len(queued)
    view.apply_bot_count()
    assert view.bot_count_report.text() == cv.TUNING_WRITE_RUNNING
    view._time_zone_reading = type("R", (), {"problem": None})()  # type: ignore[assignment]
    view._time_zone_pending = False
    view._chosen_time_zone = lambda: "UTC"  # type: ignore[method-assign]
    view.apply_time_zone()
    assert view.tuning_report.toPlainText() == cv.TUNING_WRITE_RUNNING
    assert len(queued) == jobs


@pytest.mark.parametrize("flag", ["_reset_running", "_bot_count_writing", "_time_zone_writing"])
def test_a_save_waits_for_a_reset_a_bot_count_or_a_time_zone_write_that_is_running(
    qapp: object, ps: _Ps, tmp_path: Path, flag: str
) -> None:
    """The other direction: those jobs write the same confs, and a save must not overlap them."""
    view, queued = _queued_view(ps, tmp_path, _Hold())
    setattr(view, flag, True)
    _press(view, "card_save")
    assert view.tuning_report.toPlainText() == cv.TUNING_WRITE_RUNNING
    assert _writes(queued) == []
    setattr(view, flag, False)
    _press(view, "card_save")
    assert len(_writes(queued)) == 1, "once it is over the save is taken"


def test_those_presses_are_taken_again_once_the_save_has_landed(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_controller_view import _reset_yes

    view, queued = _pending_save(ps, tmp_path)
    _land(_writes(queued)[0])
    _reset_yes(monkeypatch)
    before = len(queued)
    _reset_all(view)
    assert len(queued) > before, "the reset was refused after the save had landed"


def test_a_reset_whose_facts_land_after_a_save_began_is_not_run(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reset asks its question from facts a job reads; a save may begin meanwhile."""
    from tests.test_controller_view import _reset_yes, _wotlk_server

    _wotlk_server(tmp_path)
    view, queued = _queued_view(ps, tmp_path, _Hold())
    object.__setattr__(view.services, "reset_settings", lambda *_a: None)
    asked: list[str] = []
    _reset_yes(monkeypatch, asked)
    view.reset_to_default(("env/dist/etc/worldserver.conf",))
    facts = [
        j for j in queued if getattr(j[1], "__func__", None) is cv.ControllerView._press_facts_ready
    ]
    assert len(facts) == 1
    _press(view, "card_save")
    assert view._tuning_writing
    jobs = len(queued)
    _land(facts[0])
    assert view.tuning_report.toPlainText() == cv.TUNING_WRITE_RUNNING
    assert asked == [] and len(queued) == jobs, "the reset went ahead beside the save"
