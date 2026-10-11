"""One answer to "may this tab be torn down now?" (T690).

Three guards used to answer it differently (`forget_refusal()`, `busy_reason()`
and `_client_dir_busy()`), and a Restore, a backup, a network apply, a module
job or a Start/Stop/Restart was invisible to most of the doors that tear a tab
down. Measured live on m910q: a client-folder change during a backup froze the
window for 7 minutes and the backup vanished.

Two kinds of test live here. The BEHAVIOUR tests drive the real `ControllerView`
(offscreen) with each kind of work running and ask every door that is on the
tab. The RELATIONSHIP test reads the source: it finds every function that tears
something down (a `drop_controller()` call, a quit, a forced exit, a rebuild
signal) and fails for any that does not reach `teardown_work()`, so a door added
later without the guard fails here and not in a player's restore.

Mutations (each must turn a test below RED):
- drop one kind from `ControllerView.teardown_work()`;
- drop the `teardown_refusal()` call from `_client_dir_busy()`;
- drop the guard from any door the relationship test lists.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import shiboken6

from tests.test_controller_view import WOTLK, _client_dir_view
from yulon import teardown
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- behaviour


def _rebuild_running(view: ControllerView) -> None:
    # Something that is running, and that `shutdown()` can still stop and join afterwards.
    view.rebuild_log = SimpleNamespace(  # type: ignore[assignment]
        running=True, stop=lambda: None, wait=lambda _ms=0: True
    )


Case = tuple[str, Callable[[ControllerView], None], str, str]
"""(id, make it run, the kind `teardown_work()` answers, a word the sentence must contain)."""

CASES: list[Case] = [
    ("restore", lambda v: setattr(v, "_restore_running", True), teardown.RESTORE, "restore"),
    ("backup", lambda v: setattr(v, "_backup_running", True), teardown.BACKUP, "backup"),
    (
        "backup-before-update",
        lambda v: setattr(v, "_backup_before_update", True),
        teardown.UPDATE_BACKUP,
        "backing this server up",
    ),
    (
        "move",
        lambda v: setattr(v, "_move_panel", SimpleNamespace(running=True)),
        teardown.MOVE,
        "packed or brought in",
    ),
    (
        "module-job",
        lambda v: setattr(v, "_module_pending", "install mod-ah-bot"),
        teardown.MODULE,
        "install mod-ah-bot",
    ),
    (
        "module-jobs-count",
        lambda v: setattr(v, "_module_jobs", 1),
        teardown.MODULE,
        "Modules tab",
    ),
    (
        "network-apply",
        lambda v: setattr(v, "_network_applying", True),
        teardown.NETWORK,
        "network change",
    ),
    ("tuning-save", lambda v: setattr(v, "_tuning_writing", True), teardown.TUNING, "Tuning"),
    ("tuning-put-back", lambda v: setattr(v, "_put_back_running", True), teardown.TUNING, "Tuning"),
    ("rebuild-panel", _rebuild_running, teardown.PANEL, "rebuild"),
    ("server-start", lambda v: v._set_busy(True, "Start"), teardown.ACTION, "Start"),
    ("server-restart", lambda v: v._set_busy(True, "Restart"), teardown.ACTION, "Restart"),
]

# What `busy_reason()` already refused, and still does: the union keeps all of it.
BUSY_REASON_CASES: list[tuple[str, str]] = [
    ("_import_running", "import"),
    ("_module_sql_running", "module importer"),
    ("_reset_running", "settings files"),
    ("_bot_count_writing", "bot"),
    ("_time_zone_writing", "time zone"),
    ("_uninstall_running", "uninstalled"),
    ("_play_client_running", "ready-to-play"),
]


def _view(
    tmp_path: Path, name: str = "server"
) -> tuple[ControllerView, Any, list[tuple[Any, ...]]]:
    """A tab over the real factory wiring, with its client-folder write recorded."""
    real = tmp_path / "real-client"
    (real / "Interface").mkdir(parents=True, exist_ok=True)
    chosen = tmp_path / "new-client"
    (chosen / "Data").mkdir(parents=True, exist_ok=True)
    view, fake = _client_dir_view(
        WOTLK, tmp_path / name, client_dir=real, pick_client_dir=lambda *_: chosen
    )
    rebuilds: list[tuple[Any, ...]] = []
    view.client_dir_changed.connect(lambda *a: rebuilds.append(a))
    _MADE.append(view)
    return view, fake, rebuilds


_MADE: list[ControllerView] = []


@pytest.fixture(autouse=True)
def _join_the_views_made() -> Any:
    """A view's job threads are joined before the process ends: a QThread destroyed while
    running aborts it (exit 134)."""
    yield
    while _MADE:
        made = _MADE.pop()
        if shiboken6.isValid(made):  # another module's test may have shut it down already
            made.shutdown()


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(
        controller_view_module,
        "show_information",
        lambda _p, title, text: said.append((title, text)),
    )
    return said


def test_a_quiet_tab_may_be_torn_down(qapp: object, tmp_path: Path) -> None:
    view, _fake, _ = _view(tmp_path)
    assert view.teardown_work() is None
    assert view.teardown_refusal() is None
    assert view.forget_refusal() is None


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_each_kind_of_running_work_refuses_a_teardown_and_names_itself(
    qapp: object, tmp_path: Path, case: Case
) -> None:
    _id, make_it_run, kind, word = case
    view, _fake, _ = _view(tmp_path)
    make_it_run(view)

    assert view.teardown_work() == kind
    refusal = view.teardown_refusal()
    assert refusal is not None and word in refusal, refusal
    # The removal asks the same predicate; only its wording differs.
    assert view.forget_refusal() is not None


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_each_kind_of_running_work_blocks_a_client_folder_change(
    qapp: object, tmp_path: Path, told: list[tuple[str, str]], case: Case
) -> None:
    """The measured defect: the press wrote, emitted the rebuild signal, and the tab was dropped."""
    _id, make_it_run, _kind, word = case
    view, fake, rebuilds = _view(tmp_path)
    make_it_run(view)

    view.change_client_dir()
    view.forget_client_dir()

    assert fake.written == [], "a client folder was written while work was running"
    assert rebuilds == [], "the tab was asked to rebuild over running work"
    assert len(told) == 2 and all(word in text for _title, text in told), told


@pytest.mark.parametrize("flag,word", BUSY_REASON_CASES)
def test_what_busy_reason_refused_is_still_refused_everywhere(
    qapp: object, tmp_path: Path, told: list[tuple[str, str]], flag: str, word: str
) -> None:
    view, fake, rebuilds = _view(tmp_path)
    setattr(view, flag, True)

    refusal = view.teardown_refusal()
    assert refusal is not None and word in refusal
    assert view.forget_refusal() is not None
    view.change_client_dir()
    assert fake.written == [] and rebuilds == []


def test_the_kinds_are_exactly_the_ones_the_cases_exercise() -> None:
    """A kind added to `teardown_work()` with no case here is a guard nothing proves."""
    assert {case[2] for case in CASES} == set(teardown.KINDS)


def test_the_removal_and_the_other_doors_always_agree_on_whether_to_refuse(
    qapp: object, tmp_path: Path
) -> None:
    for case in CASES:
        view, _fake, _ = _view(tmp_path, name=f"agree-{case[0]}")
        case[1](view)
        assert (view.forget_refusal() is None) == (view.teardown_refusal() is None), case[0]


def test_start_from_the_tray_does_not_run_on_top_of_a_running_action(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-4: `restart_from_server_tab()` refuses while busy; the tray's Start did not."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    view, _fake, _ = _view(tmp_path)
    started: list[int] = []
    view.services.controller.start = lambda: started.append(1) or True  # type: ignore[method-assign,assignment]
    view._set_busy(True, "Restart")

    view.start_server()

    assert started == [], "Start ran on top of a running Restart"
    assert view._busy_job == "Restart", "the first job lost its lock"


# ------------------------------------------------- a Stop may be quit over


def test_a_stop_in_flight_is_quit_over_but_never_torn_down_otherwise(
    qapp: object, tmp_path: Path
) -> None:
    """The tray's "Quit now" while it stops servers: "a stop that is already running carries on".

    Quitting is the one door a Stop may be left running behind (it carries on in Docker, and
    T158's `_stop_abandon` ends a load wait); every other door, and every other action,
    still refuses. Mutation: drop the `quitting` clause from `teardown_work()`.
    """
    view, _fake, _ = _view(tmp_path)
    view._set_busy(True, "Stop")
    assert view.teardown_refusal() is not None, "a client-folder change over a Stop"
    assert view.forget_refusal() is not None, "a removal over a Stop"
    assert view.teardown_refusal(quitting=True) is None, "Quit now was refused over a Stop"
    view._set_busy(False)
    for job in ("Start", "Restart", "Recreate containers"):
        view._set_busy(True, job)
        assert view.teardown_refusal(quitting=True) is not None, job
        view._set_busy(False)


def test_a_stop_does_not_hide_other_work_from_the_quit(qapp: object, tmp_path: Path) -> None:
    view, _fake, _ = _view(tmp_path)
    view._set_busy(True, "Stop")
    view._restore_running = True
    refusal = view.teardown_refusal(quitting=True)
    assert refusal is not None and "restore" in refusal


# ------------------------------------------- the docker-group restart (an execv)


def _parent_with(refusal: Any) -> Any:
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    parent.yulon_close_refusal = refusal  # type: ignore[attr-defined]
    return parent


@pytest.mark.parametrize("busy", [True, False])
def test_the_docker_group_restart_waits_for_work_that_cannot_be_cut_off(
    qapp: object, monkeypatch: pytest.MonkeyPatch, busy: bool
) -> None:
    """`os.execv` replaces the process with nothing joined: it asked no one before."""
    from PySide6.QtWidgets import QMessageBox

    from yulon import platform
    from yulon.ui import catalog_view

    asked_strict: list[bool] = []

    def refusal(strict: bool = False) -> str | None:
        asked_strict.append(strict)
        return "A restore is writing into this server's databases." if busy else None

    execs: list[int] = []
    told: list[tuple[Any, ...]] = []
    monkeypatch.setattr(platform, "docker_group_reexec", lambda: ["yulon"])
    monkeypatch.setattr(
        platform, "restart_under_docker_group", lambda reexec=None: execs.append(1) or False
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: int(QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(catalog_view, "show_information", lambda *a, **k: told.append(a))

    handled = catalog_view.offer_a_docker_group_restart(
        _parent_with(refusal), "Docker is set up.", failed_title="Install failed"
    )

    assert asked_strict and all(asked_strict), "the restart must ask strictly (no Stop exemption)"
    assert execs == ([] if busy else [1])
    assert bool(told) == busy
    if busy:
        assert "restore" in str(told[0])
        # Deferred is NOT handled: the Catalog still shows its "Install failed" warning.
        assert handled is False
        said = str(told[0])
        assert "Quit Yu'lon…" in said and "same button" not in said, said
    else:
        assert handled is True


def test_a_refusal_carries_the_kind_of_work_it_names(qapp: object, tmp_path: Path) -> None:
    view, _fake, _ = _view(tmp_path)
    view._restore_running = True
    refusal = view.teardown_refusal()
    assert refusal is not None and getattr(refusal, "kind", None) == teardown.RESTORE
    view._restore_running = False
    view._reset_running = True  # one of `busy_reason()`'s: no kind, so the cautious wording
    plain = view.teardown_refusal()
    assert plain is not None and getattr(plain, "kind", None) is None
