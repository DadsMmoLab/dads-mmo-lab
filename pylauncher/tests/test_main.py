"""Tests for the launcher entry point: headless provisioning, and tab wiring.

`main.py` had no tests at all before this file. What needed pinning first is
`--provision`, because its EXIT CODES are control flow for something else: the
clean-Windows harness reboots on 3 and stops on 2, so a code that drifts does
not produce a wrong message, it produces a harness that reboots forever or
gives up on a box that was fine.

The second half covers `build_window()`'s tab bookkeeping, which the packaging
smoke test (`YULON_SMOKE_TEST`) only ever proved could be built once. Those
tests drive the window through the signals the catalog view really emits,
because reaching past the signal into the closure is how the bug they pin
survived review in the first place.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import main
from tests.conftest import process_events, pump_until
from yulon import platform, state, update

_REAL_LOAD_STATE = state.load_state
"""Captured at import, before the module fixture replaces it with a stand-in.

The window fixture patches `state.load_state` for the whole module, so a test
about the REAL function has to hold on to it from before that happened.
"""


def _report(**kwargs: Any) -> platform.ProvisionReport:
    base: dict[str, Any] = {"platform": "windows"}
    base.update(kwargs)
    return platform.ProvisionReport(**base)


@pytest.fixture(autouse=True)
def _no_real_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here may install Docker on the machine running the tests."""

    def _refuse(*_args: Any, **_kwargs: Any) -> platform.ProvisionReport:
        raise AssertionError("ensure_docker() was called for real")

    monkeypatch.setattr(main.platform, "ensure_docker", _refuse)
    monkeypatch.setattr(main.platform, "docker_program", lambda: "docker")


def test_a_ready_daemon_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.platform, "ensure_docker", lambda **_k: _report(docker_ready=True))
    assert main.provision_headless() == main.PROVISION_READY == 0


def test_a_required_reboot_is_its_own_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wsl --install` forces a reboot on a box with no WSL, which this checkpoint is.

    It must not share an exit code with "needs a human": the harness reboots and
    runs another pass for one and stops for the other. Note `docker_ready` is
    True here as well — a reboot outranks it, because nothing after the reboot
    has been judged yet.
    """
    monkeypatch.setattr(
        main.platform,
        "ensure_docker",
        lambda **_k: _report(docker_ready=True, reboot_required=True),
    )
    assert main.provision_headless() == main.PROVISION_REBOOT == 3


def test_a_daemon_that_never_came_up_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main.platform,
        "ensure_docker",
        lambda **_k: _report(manual_steps=("start Docker Desktop yourself",)),
    )
    assert main.provision_headless() == main.PROVISION_MANUAL == 2


def test_the_three_exit_codes_are_distinct() -> None:
    """They are a protocol. Two of them colliding is silent and total."""
    codes = {main.PROVISION_READY, main.PROVISION_MANUAL, main.PROVISION_REBOOT}
    assert len(codes) == 3


def test_the_report_is_one_parseable_line_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The harness greps one line out of a log that also carries human logging.

    A step whose text contains a newline is the case that breaks a naive
    emitter, and installers produce those — so it is the case tested.
    """
    monkeypatch.setattr(
        main.platform,
        "ensure_docker",
        lambda **_k: _report(
            done=("downloaded the installer\nto C:\\x",),
            skipped=("start Docker Desktop: no exe",),
            docker_ready=False,
        ),
    )
    main.provision_headless()
    marked = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("YULON_PROVISION_JSON ")
    ]
    assert len(marked) == 1, "the harness needs exactly one marked line"
    payload = json.loads(marked[0][len("YULON_PROVISION_JSON ") :])
    assert payload["done"] == ["downloaded the installer\nto C:\\x"]
    assert payload["ok"] is False
    assert payload["docker_cli"] == "docker"
    assert set(payload) == {
        "platform",
        "done",
        "skipped",
        "manual_steps",
        "reboot_required",
        "docker_ready",
        "ok",
        "docker_cli",
        "docker_group",
    }
    # The consent outcome is part of the support payload: it is what tells
    # "the user declined root-equivalent access" apart from "provisioning
    # broke", and headless can only ever report the former.
    assert payload["docker_group"] == "not-applicable"


def test_an_unresolvable_docker_cli_is_reported_as_null(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The field that distinguishes "installed it" from "can now use it".

    That gap is Cross-cutting defect 3 and it is invisible from anywhere else in
    the report: every step can read as done while the process that ran them
    still cannot spell `docker`.
    """
    monkeypatch.setattr(main.platform, "docker_program", lambda: None)
    monkeypatch.setattr(main.platform, "ensure_docker", lambda **_k: _report(docker_ready=False))
    main.provision_headless()
    line = next(
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("YULON_PROVISION_JSON ")
    )
    assert json.loads(line[len("YULON_PROVISION_JSON ") :])["docker_cli"] is None


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["yulon", "--provision"], {}),
        (["yulon"], {"YULON_PROVISION": "1"}),
    ],
    ids=["flag", "environment"],
)
def test_main_takes_the_headless_path_without_building_a_window(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], env: dict[str, str]
) -> None:
    """Both spellings, and neither may import Qt.

    The environment variable exists because a scheduled task is a clumsy place
    to pass arguments; the flag exists because a support request should be one
    thing to type. Qt not being imported is the load-bearing half: this runs on
    a box that may have no display at all.
    """
    monkeypatch.setattr(main, "configure", lambda **_k: None)
    monkeypatch.setattr(main.sys, "argv", argv)
    # Cleared first: a developer with YULON_PROVISION exported would otherwise
    # make the flag case pass without the flag doing anything.
    monkeypatch.delenv("YULON_PROVISION", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    def _boom() -> object:
        raise AssertionError("build_window() was called in headless provisioning mode")

    monkeypatch.setattr(main, "build_window", _boom)
    monkeypatch.setattr(main.platform, "ensure_docker", lambda **_k: _report(docker_ready=True))
    assert main.main() == 0


def test_the_report_line_survives_a_console_that_cannot_spell_the_step_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """This crashed a real clean-Windows run, at the moment it reported success.

    `platform`'s own step text contains an arrow, and the harness runs the frozen
    app as `yulon.exe --provision > log 2>&1`, which gives a cp1252 stdout. The
    first version of this function passed `ensure_ascii=False` for prettier
    output and died with UnicodeEncodeError right here -- after the run had
    already spent a 659 MB download (clean-box run, 2026-08-23).

    So the marked line has to be encodable by the narrowest console encoding it
    can plausibly meet, and the escaping has to be lossless: a harness that reads
    a mangled path is no better off than one that reads nothing.
    """
    step = r"downloaded the installer → C:\Users\user\x.exe"
    monkeypatch.setattr(
        main.platform,
        "ensure_docker",
        lambda **_k: _report(done=(step,), docker_ready=True),
    )
    main.provision_headless()
    line = next(
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("YULON_PROVISION_JSON ")
    )
    line.encode("cp1252")  # the whole assertion: this is what raised
    payload = json.loads(line[len("YULON_PROVISION_JSON ") :])
    assert payload["done"] == [step], "the escaping lost or changed the step text"


def test_headless_provisioning_never_hands_over_a_prompter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--provision` has nobody to ask, and that has to be the mechanism, not a hope.

    `main.py`'s docstring and the payload comment both claim headless on Linux
    always answers "not-asked". Every test here replaced `ensure_docker`
    wholesale with a stub whose report defaults `docker_group` to
    "not-applicable", so the claim was asserted in prose in two files and
    verified in neither: a regression that passed a live prompter here would
    not have failed anything (review, 2026-08-24).
    """
    seen: list[dict[str, Any]] = []

    def _provision(**kwargs: Any) -> platform.ProvisionReport:
        seen.append(kwargs)
        return _report(docker_ready=True)

    monkeypatch.setattr(main.platform, "ensure_docker", _provision)
    assert main.provision_headless() == main.PROVISION_READY
    assert seen and seen[0].get("ask") is None


# ------------------------------------------- a config dir that cannot be written

# Run in a child process on purpose. The failure is in `main()`'s FIRST
# statement, so the only honest reproduction starts an interpreter that has not
# configured logging yet, and a suite that has already built a `QApplication`
# cannot build the second one `main()` makes. `build_window()` is the one thing
# stubbed: the real one reads the user's `state.json` and asks GitHub for a
# release, neither of which belongs in this test - and neither of which is ever
# reached when the entry point dies at line one.
_ENTRY_POINT = """\
import os, pathlib, sys

sys.argv = ["yulon"]
from yulon import platform

blocked = pathlib.Path(os.environ["YULON_TEST_BLOCKED"])
resolved = platform.config_dir()
if blocked not in resolved.parents:
    raise SystemExit(f"config_dir() ignored the environment and answered {resolved}")

from PySide6.QtWidgets import QMainWindow

import main

main.build_window = lambda: QMainWindow()
raise SystemExit(main.main())
"""


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="config_dir() has no environment override on macOS, so nothing here can block it",
)
def test_the_launcher_still_starts_when_its_config_dir_cannot_be_written(
    tmp_path: Path,
) -> None:
    """The whole defect, at the entry point: an unwritable config dir killed startup.

    `configure(config_dir=platform.config_dir())` runs before `QApplication`
    exists, and `RotatingFileHandler` was constructed with no `try` anywhere
    between `__main__` and it - so a managed profile, a read-only roaming share
    or a redirected `%APPDATA%` got `PermissionError` and exit 1 with no window.
    The shipped exe is `console=False` (`build/pylauncher.spec`), so it produced
    no window, no dialog and not even a visible traceback.

    The temp dir is pointed at a writable place as well, because "it started" is
    only half the fix: support gets nothing out of an app that came up with no
    log at all, and this asserts the log really landed where the fallback says.
    """
    blocked = tmp_path / "roaming"
    blocked.write_text("a file, so nothing can be created under it", encoding="utf-8")
    scratch_temp = tmp_path / "temp"
    scratch_temp.mkdir()

    env = dict(os.environ)
    env.update(
        {
            "YULON_TEST_BLOCKED": str(blocked),
            "APPDATA": str(blocked),  # Windows: the reported trigger
            "XDG_DATA_HOME": str(blocked),  # Linux: the same question, its own variable
            "YULON_SMOKE_TEST": "1",  # build the window, then leave; do not run an event loop
            "QT_QPA_PLATFORM": "offscreen",
            "TMPDIR": str(scratch_temp),
            "TEMP": str(scratch_temp),
            "TMP": str(scratch_temp),
        }
    )
    env.pop("YULON_PROVISION", None)
    pylauncher = Path(main.__file__).parent
    env["PYTHONPATH"] = str(pylauncher)

    done = subprocess.run(
        [sys.executable, "-c", _ENTRY_POINT],
        cwd=pylauncher,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert done.returncode == 0, f"the launcher refused to start:\n{done.stderr}"
    fallback_log = scratch_temp / "yulon" / "yulon.log"
    assert fallback_log.exists(), f"it started but logged nowhere:\n{done.stderr}"
    assert "Yu'lon launcher starting" in fallback_log.read_text(encoding="utf-8")


# ------------------------------------------- the GUI thread, declared not inferred

# A child process for the same reason `_ENTRY_POINT` above needs one: the fact
# under test is set beside `QApplication(sys.argv)`, and a suite that already
# holds one `QApplication` cannot build the second. In-process this could only
# ever be a grep.
_GUI_THREAD_ENTRY_POINT = """\
import sys
import threading

sys.argv = ["yulon"]
from PySide6.QtWidgets import QMainWindow

import main
from yulon import platform

if platform.gui_thread() is not None:
    raise SystemExit(f"named a GUI thread before starting one: {platform.gui_thread()!r}")

main.build_window = lambda: QMainWindow()
code = main.main()
if code != 0:
    raise SystemExit(f"the smoke-test start exited {code}")
if platform.gui_thread() is not threading.main_thread():
    raise SystemExit(
        "main() built a QApplication without declaring its GUI thread: "
        f"gui_thread() is {platform.gui_thread()!r}"
    )
"""


def test_the_launcher_declares_which_thread_is_its_gui_thread(tmp_path: Path) -> None:
    """bug-checklist §43: the Windows keep-awake refusal reads a declaration, so it must arrive.

    `platform.keep_awake()` refuses `platform.gui_thread()` and nothing else.
    If `main()` ever stops calling `declare_gui_thread()`, that refusal goes
    quiet — the GUI could then hold a thread-scoped assertion on a worker's
    behalf and no test of `platform.py` alone would notice. So this starts the
    real entry point in a child process and fails on the missing declaration,
    rather than grepping `main.py` for the call.

    It also pins the other half: a process that has NOT started a window names
    no GUI thread, which is what makes the headless harness's own main thread
    allowed to hold it.
    """
    scratch_temp = tmp_path / "temp"
    scratch_temp.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    env = dict(os.environ)
    env.update(
        {
            "APPDATA": str(home),
            "XDG_DATA_HOME": str(home),
            "YULON_SMOKE_TEST": "1",  # build the window, then leave; do not run an event loop
            "QT_QPA_PLATFORM": "offscreen",
            "TMPDIR": str(scratch_temp),
            "TEMP": str(scratch_temp),
            "TMP": str(scratch_temp),
        }
    )
    env.pop("YULON_PROVISION", None)
    pylauncher = Path(main.__file__).parent
    env["PYTHONPATH"] = str(pylauncher)

    done = subprocess.run(
        [sys.executable, "-c", _GUI_THREAD_ENTRY_POINT],
        cwd=pylauncher,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"


def test_a_log_that_had_to_move_is_told_to_the_user_and_not_only_to_the_log(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stderr warning about the log reaches nobody in the build that ships.

    `build/pylauncher.spec` sets `console=False`, so the frozen exe has no
    stream to warn on: a dialog is the only channel that survives the
    packaging, and it names the file so the user can find it.
    """
    from PySide6.QtWidgets import QMessageBox

    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: told.append(a[2]))
    monkeypatch.setattr(main, "file_log_problem", lambda: r"logging to C:\Temp\yulon\yulon.log")

    main._warn_about_the_log_file(None)

    assert told and r"C:\Temp\yulon\yulon.log" in told[0], f"the user was not told: {told}"


def test_a_log_that_went_where_it_was_asked_to_says_nothing(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every normal start goes through this line; a dialog on it would be a new bug."""
    from PySide6.QtWidgets import QMessageBox

    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: told.append(a[2]))
    monkeypatch.setattr(main, "file_log_problem", lambda: None)

    main._warn_about_the_log_file(None)

    assert told == [], f"a working install was nagged: {told}"


def test_a_state_file_that_cannot_be_written_does_not_look_like_a_saved_one(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same unwritable directory, reached from a Qt slot instead of from startup.

    Swallowing it would leave the new tab on screen and the install forgotten,
    which is indistinguishable from a save that worked right up until the next
    launch comes up without it.
    """
    from PySide6.QtWidgets import QMessageBox

    def _refuse(*_a: Any, **_k: Any) -> Any:
        raise PermissionError(13, "Access is denied", "state.json")

    monkeypatch.setattr(state, "save_state", _refuse)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: told.append(a[2]))

    assert main._warn_unless_remembered(state.AppState(), None) is False
    assert told and "state.json" in told[0], f"the failed save was silent: {told}"


def test_a_state_file_that_was_written_is_reported_as_written(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: the caller has to be able to tell the two apart."""
    saved: list[Any] = []
    monkeypatch.setattr(state, "save_state", lambda app_state, path=None: saved.append(app_state))

    assert main._warn_unless_remembered(state.AppState(), None) is True
    assert len(saved) == 1


# --------------------------------------------------------------- window tabs


@pytest.fixture(scope="module")
def _app_window(qapp: object) -> Iterator[Any]:
    """ONE real window, built through `main.build_window()`, shared by this module.

    Repeatedly building windows is what made this file crash. Measured on Linux
    with offscreen Qt (PySide6 6.11.2), 25 runs per count:

        1 window  0/25   2 windows  2/25   3 windows  3/25
        4 windows 8/25   5 windows  9/25

    A dose-response, not a bug in any one test: each of the five passed 25/25
    alone. It surfaced as a SEGFAULT (and sometimes SIGBUS) inside whichever
    test happened to be allocating at the time - `state.remember()`, a signal
    emit - which is why three attempts at making teardown safer all measured as
    noise. Only the count matters, so there is one window and the tests share
    it.

    Sharing is safe because tabs are keyed by (game, server_dir): every test
    below uses its own directory, creates its own tab through the real signals,
    and cannot see another's. Nothing is pre-seeded into `state.json` - a tab
    arrives the way a user's does.

    Three things `build_window()` does are unacceptable in a unit test and are
    neutralised here: it reads the user's real `state.json`, writes it back
    whenever a tab is added, and starts a thread that asks GitHub for the
    latest release.

    **The update check is patched HERE and not in a test**, and so is
    `update.json`. `build_window()` imports `check_with_cache` into its own
    namespace on the way in, so a patch applied after the window exists is
    never seen by it — the window would already have asked GitHub. And this
    fixture is MODULE-scoped, so pytest builds it BEFORE the function-scoped
    autouse fixture in `conftest.py` that points `platform.config_dir()` at a
    scratch directory. Measured on this box, 2026-09-20, with a probe module
    that printed both: at module-fixture time `config_dir()` answered
    `~/.local/share/yulon` — the developer's own — and inside the test body it
    answered a `tmp_path`. So the redirect every other test relies on is simply
    not up yet here, and an unpatched check would have written the real
    `update.json`. Both doors are shut before `build_window()` is called.
    """
    import tempfile

    from PySide6.QtWidgets import QApplication

    from yulon import update_state
    from yulon.ui.controller_view import ControllerView

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(update, "check_with_cache", lambda **kwargs: None)
    scratch = Path(tempfile.mkdtemp(prefix="yulon-test-update-state-"))
    monkeypatch.setattr(
        update_state, "update_state_path", lambda config_dir=None: scratch / "update.json"
    )
    monkeypatch.setattr(
        state,
        "load_state",
        lambda path=None, repair=True: state.AppState(installs=[]),
    )
    # Captured here rather than in the test that reads it: `build_window()`
    # imports `save_state` into its own namespace on the way in, so a patch
    # applied after the window exists is never seen by the window.
    saved: list[Any] = []
    monkeypatch.setattr(state, "save_state", lambda app_state, path=None: saved.append(app_state))

    # A test about which tab exists must not also be shelling out to docker on a
    # 5-second timer, into a worker thread that outlives the assertions.
    real_init = ControllerView.__init__

    def _no_polling(self: Any, entry: Any, services: Any, **kwargs: Any) -> None:
        kwargs["status_poll_ms"] = 0
        real_init(self, entry, services, **kwargs)

    monkeypatch.setattr(ControllerView, "__init__", _no_polling)

    # T179: the start-up sweep of temporary client copies an Uninstall left. It
    # would read the REAL config dir here (see above), so it is recorded instead,
    # with the thread it ran on, and answers a notice the test can look for.
    import threading

    swept: list[str] = []

    def _sweep(*, config_dir: Path | None = None) -> Any:
        swept.append(threading.current_thread().name)
        return main.LeftoverNotice(LEFTOVER_NOTICE_SEEN, ("/nowhere/a copy",))

    monkeypatch.setattr(main, "sweep_leftover_client_copies", _sweep)
    from yulon.ui.widgets.update_bar import UpdateBar

    bar_said: list[str] = []

    bar_callbacks: list[Any] = []

    def _offer(self: Any, text: str, on_shown: Any = None) -> None:
        # The record is made when the notice is SHOWN (fix round 2): kept for the test.
        bar_callbacks.append(on_shown)
        # Recorded, not shown: the window is shared, and a notice on the bar (or one
        # waiting to be shown when it clears) would change what the update tests read.
        # `test_update_bar_notice.py` holds what the bar does with it.
        bar_said.append(text)

    monkeypatch.setattr(UpdateBar, "offer_notice", _offer)

    window = main.build_window()
    # Read before any test adds a tab: the window as a player with no servers opens it.
    header = window.property("header")
    window.header_badge_hidden_at_start = header is not None and header._badge.isHidden()
    window.swept = swept
    window.bar_said = bar_said
    window.bar_callbacks = bar_callbacks
    window.saved_states = saved
    window.update_state_dir = scratch
    yield window

    # `_stop_background_threads` itself, not a hand-rolled equivalent: a QThread
    # destroyed while running ABORTS the process, and a leaked one takes the
    # whole run down with it.
    main._stop_background_threads(window)
    QApplication.processEvents()
    monkeypatch.undo()
    shutil.rmtree(scratch, ignore_errors=True)


LEFTOVER_NOTICE_SEEN = "a leftover copy is still there (test)"
_REAL_SWEEP = main.sweep_leftover_client_copies
"""The real sweep: `_app_window` replaces the module's for the whole module."""


@pytest.fixture
def window(_app_window: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The shared window, with `save_state` captured for the test that reads it."""
    return _app_window


def _catalog_view(window: Any) -> Any:
    """The window's CatalogView, found the way a click reaches it: through the widget tree.

    Imported here rather than at module scope so the headless tests above still
    run on a box with no display and no Qt (`--provision` must not need either).
    """
    from yulon.ui.catalog_view import CatalogView

    view = window.findChild(CatalogView)
    assert view is not None
    return view


def _tab_for(window: Any, server_dir: Any) -> Any:
    """The controller tab this test owns, by the key tabs are stored under."""
    for view in window.yulon_controllers:
        if view.services.controller.server_dir == server_dir:
            return view
    raise AssertionError(f"no tab for {server_dir}")


# ------------------------------------------------------------- the update check

A_RELEASE = update.UpdateCheck(
    "0.8.66-Public",
    "v0.8.70-Public",
    True,
    "https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v0.8.70-Public",
    notes=(update.ReleaseNotes("v0.8.70-Public", "- Ten.", False),),
)


class _FakeDialog:
    """`UpdateDialog` with a non-blocking `exec()`.

    A real one is application-modal, and `conftest.py`'s `_no_modal_dialogs`
    covers `QMessageBox` only — a `QDialog.exec()` in an offscreen run waits for
    a click that never comes, at zero CPU, with no failure and no output. The
    factory is injectable for exactly this.

    `deleteLater` is here because the host calls it on whatever the factory
    returned, which is the fix for the dialog that used to be left parented to
    the window on every click.
    """

    def __init__(self, choice: Any) -> None:
        self.choice = choice
        self.shown = 0
        self.deleted = 0

    def exec(self) -> int:
        self.shown += 1
        return 1

    def deleteLater(self) -> None:
        self.deleted += 1


def _opens_into(opened: list[str]) -> Any:
    """An opener that records and says it worked. `Callable[[str], bool]`.

    The `True` is the contract, not decoration: an opener that answers False —
    which `QDesktopServices.openUrl()` really does on a box with no browser —
    puts the URL on the bar instead.
    """

    def opener(url: str) -> bool:
        opened.append(url)
        return True

    return opener


@pytest.fixture
def update_host(window: Any) -> Iterator[Any]:
    """The window's update host, with its bar and its `update.json` clean at both ends.

    One window is shared by this whole module (`_app_window`), so a test that
    skipped a version or left the bar up would be handing the next one a state
    it never set. The four injectable attributes are put back too — a stand-in
    `make_dialog` left on a shared window is how a later test ends up asserting
    about a dialog this one built.
    """
    host = window.property("update_host")
    assert host is not None
    # The launch check has its own QThread and refuses a manual check while it
    # runs (by design). Every test below is about the settled app, which is the
    # state a user reaches long before they find the button.
    startup = host.startup_thread
    if startup is not None:
        pump_until(lambda: not startup.isRunning(), "the launch update check finished")
    seams = {name: getattr(host, name) for name in ("check", "run_job", "make_dialog", "open_url")}
    path = window.update_state_dir / "update.json"
    path.unlink(missing_ok=True)
    yield host
    window.property("update_bar").clear()
    for name, seam in seams.items():
        setattr(host, name, seam)
    # What a test can leave behind on the host itself: the release the dialog
    # would open on, and a check it never let finish — which would make every
    # test after it click a dead button.
    host._offered = None
    host._checking = False
    _check_button(window).setEnabled(True)
    path.unlink(missing_ok=True)


def test_a_startup_check_shows_the_bar_for_a_release_that_is_newer(
    window: Any, update_host: Any
) -> None:
    update_host.startup_result(A_RELEASE)

    bar = window.property("update_bar")
    assert not bar.isHidden()
    assert "v0.8.70-Public" in bar.text() and "0.8.66-Public" in bar.text()


def test_a_skipped_version_is_not_announced_at_startup(window: Any, update_host: Any) -> None:
    """ "Skip this version" is about the launch after it, which is this one."""
    update.skip_version("v0.8.70-Public")

    update_host.startup_result(A_RELEASE)

    assert window.property("update_bar").isHidden()


def test_a_startup_check_that_failed_says_nothing(window: Any, update_host: Any) -> None:
    """Offline at launch is the normal case, not news."""
    update_host.startup_result(dataclasses.replace(A_RELEASE, available=False, error="dns"))

    assert window.property("update_bar").isHidden()


def test_a_manual_check_that_failed_says_so(window: Any, update_host: Any) -> None:
    """The 2026-08-31 post-mortem was a check that failed silently."""
    update_host.manual_result(dataclasses.replace(A_RELEASE, available=False, error="dns"))

    assert window.property("update_bar").text() == "Could not check for updates: dns"


def test_a_manual_check_that_raised_says_so_too(window: Any, update_host: Any) -> None:
    """`job.py`'s `on_error` carries the exception itself, not a string."""
    update_host.manual_failed(OSError("no route"))

    assert "no route" in window.property("update_bar").text()


def test_a_manual_check_with_nothing_newer_says_which_version_you_have(
    window: Any, update_host: Any
) -> None:
    update_host.manual_result(
        dataclasses.replace(A_RELEASE, available=False, latest="v0.8.66-Public")
    )

    assert window.property("update_bar").text() == "You have the newest version (0.8.66-Public)."


def test_a_manual_check_shows_a_version_the_player_skipped(window: Any, update_host: Any) -> None:
    """They pressed the button: hiding the answer to a question just asked is the same bug."""
    update.skip_version("v0.8.70-Public")

    update_host.manual_result(A_RELEASE)

    assert "v0.8.70-Public" in window.property("update_bar").text()


def test_the_button_in_the_header_runs_a_forced_check_and_shows_its_answer(
    window: Any, update_host: Any
) -> None:
    """Through the real button, not the slot: the wiring is what this pins."""
    from PySide6.QtWidgets import QPushButton

    from yulon.ui.widgets.job import run_inline

    asked: list[int] = []

    def check() -> object:
        asked.append(1)
        return A_RELEASE

    update_host.check = check
    update_host.run_job = run_inline

    button = window.findChild(QPushButton, "check-for-updates")
    assert button is not None
    button.click()

    assert asked == [1]
    assert "v0.8.70-Public" in window.property("update_bar").text()


def test_skip_in_the_dialog_is_written_down_and_takes_the_bar_away(
    window: Any, update_host: Any
) -> None:
    from yulon.ui.widgets.update_dialog import UpdateChoice

    update_host.startup_result(A_RELEASE)
    dialog = _FakeDialog(UpdateChoice.SKIP)
    update_host.make_dialog = lambda result: dialog

    window.property("update_bar").details_button.click()

    assert dialog.shown == 1
    assert update.load_update_state().skipped_version == "v0.8.70-Public"
    assert window.property("update_bar").isHidden()


def test_later_in_the_dialog_changes_nothing(window: Any, update_host: Any) -> None:
    from yulon.ui.widgets.update_dialog import UpdateChoice

    update_host.startup_result(A_RELEASE)
    update_host.make_dialog = lambda result: _FakeDialog(UpdateChoice.LATER)
    opened: list[str] = []
    update_host.open_url = _opens_into(opened)

    window.property("update_bar").details_button.click()

    assert opened == []
    assert update.load_update_state().skipped_version is None
    assert not window.property("update_bar").isHidden()


def test_update_now_opens_the_release_page_and_replaces_nothing(
    window: Any, update_host: Any
) -> None:
    """Plan 2 offers the download page; plan 3 is what installs it."""
    from yulon.ui.widgets.update_dialog import UpdateChoice

    update_host.startup_result(A_RELEASE)
    update_host.make_dialog = lambda result: _FakeDialog(UpdateChoice.UPDATE)
    opened: list[str] = []
    update_host.open_url = _opens_into(opened)

    window.property("update_bar").details_button.click()

    assert opened == [A_RELEASE.url]


def test_a_release_url_from_somewhere_else_is_never_handed_to_the_desktop(
    window: Any, update_host: Any
) -> None:
    """`html_url` is the feed's string, and the desktop starts whatever its scheme says.

    Driven through the button, not through `safe_release_url` — the rule was
    already unit-tested, and what this pins is that the click goes through it.
    """
    from yulon.ui.widgets.update_dialog import UpdateChoice

    hostile = dataclasses.replace(A_RELEASE, url="file:///etc/passwd")
    update_host.startup_result(hostile)
    update_host.make_dialog = lambda result: _FakeDialog(UpdateChoice.UPDATE)
    opened: list[str] = []
    update_host.open_url = _opens_into(opened)

    window.property("update_bar").details_button.click()

    assert opened == [update.RELEASES_PAGE]


# ------------------------------- when there is no browser to open (yulon-arch)


def _press_update_now(window: Any, update_host: Any, result: Any = None) -> None:
    """The real path: a release is offered, the dialog opens, "Update now" is pressed."""
    from yulon.ui.widgets.update_dialog import UpdateChoice

    update_host.startup_result(result if result is not None else A_RELEASE)
    update_host.make_dialog = lambda offered: _FakeDialog(UpdateChoice.UPDATE)
    window.property("update_bar").details_button.click()


def _clipboard() -> Any:
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.clipboard()


def test_a_browser_that_will_not_open_leaves_the_player_the_url(
    window: Any, update_host: Any
) -> None:
    """Measured on yulon-arch, 2026-09-21: no browser and no `xdg-open` on the box.

    The dialog closed and nothing happened and nothing was said — the player
    had no route to the release at all. `QDesktopServices.openUrl()` returns a
    bool and the app threw it away.
    """
    _clipboard().setText("something else")
    update_host.open_url = lambda url: False

    _press_update_now(window, update_host)

    bar = window.property("update_bar")
    assert not bar.isHidden()
    assert A_RELEASE.url in bar.text()
    assert "Could not open a browser" in bar.text()
    assert "clipboard" in bar.text(), "the message has to say the URL was copied"
    assert _clipboard().text() == A_RELEASE.url


def test_an_opener_that_raises_is_the_same_as_one_that_refuses(
    window: Any, update_host: Any
) -> None:
    """A desktop helper that is missing can raise instead of answering False."""

    def explodes(url: str) -> bool:
        raise OSError("no xdg-open on this machine")

    update_host.open_url = explodes

    _press_update_now(window, update_host)

    bar = window.property("update_bar")
    assert A_RELEASE.url in bar.text() and "Could not open a browser" in bar.text()
    assert _clipboard().text() == A_RELEASE.url


def test_a_browser_that_opens_says_nothing(window: Any, update_host: Any) -> None:
    """The bar keeps the offer; success is not news."""
    opened: list[str] = []
    update_host.open_url = _opens_into(opened)

    _press_update_now(window, update_host)

    bar = window.property("update_bar")
    assert opened == [A_RELEASE.url]
    assert "Could not open" not in bar.text()
    assert A_RELEASE.latest in bar.text(), "the bar still shows the offer"


def test_the_url_on_the_bar_is_the_vetted_one_and_never_the_feeds(
    window: Any, update_host: Any
) -> None:
    """A hostile `html_url` must not be put on screen, or in the clipboard, either."""
    update_host.open_url = lambda url: False
    hostile = dataclasses.replace(A_RELEASE, url="file:///etc/passwd")

    _press_update_now(window, update_host, hostile)

    bar = window.property("update_bar")
    assert update.RELEASES_PAGE in bar.text()
    assert "/etc/passwd" not in bar.text()
    assert _clipboard().text() == update.RELEASES_PAGE


def test_the_message_arrives_even_for_a_version_the_player_skipped(
    window: Any, update_host: Any
) -> None:
    """They asked for it by pressing the button; the bar may not stay hidden."""
    update.skip_version(str(A_RELEASE.latest))
    update_host.open_url = lambda url: False

    update_host.manual_result(A_RELEASE)
    _press_update_now(window, update_host)

    bar = window.property("update_bar")
    assert not bar.isHidden()
    assert A_RELEASE.url in bar.text()


def test_a_failed_open_does_not_cost_the_player_the_offer(window: Any, update_host: Any) -> None:
    """The message must not be the end of the road to the release.

    `show_message` hides "See what's new", so after the browser failed the only
    way back to the dialog was another manual check — and the modal dialog is
    up while the message is put on the bar.
    """
    update_host.open_url = lambda url: False

    _press_update_now(window, update_host)

    bar = window.property("update_bar")
    assert not bar.isHidden()
    assert not bar.details_button.isHidden(), "the way back to the release notes is gone"

    bar.details_button.click()  # and it still opens the dialog
    assert "Could not open a browser" in bar.text()


def test_a_message_with_no_offer_behind_it_keeps_no_button(window: Any, update_host: Any) -> None:
    """The ordinary case is unchanged: nothing to open, no button."""
    update_host.manual_result(
        dataclasses.replace(A_RELEASE, available=False, latest="v0.8.66-Public")
    )

    bar = window.property("update_bar")
    assert "You have the newest version" in bar.text()
    assert bar.details_button.isHidden()
    assert bar.fading(), "the answer to a check must clear itself (T179 fix round 2)"


def test_a_link_in_the_notes_is_not_announced_as_the_download_page(
    window: Any, update_host: Any
) -> None:
    """A release body can link anywhere; "The download page is: …" would vouch for it.

    The label has to stay neutral, or a host the body chose is put on the
    clipboard under a sentence the app's own update flow lends its authority to.
    """
    update_host.open_url = lambda url: False
    dialog = update_host.make_dialog(A_RELEASE)
    try:
        from PySide6.QtCore import QUrl

        dialog.notes.anchorClicked.emit(QUrl("https://evil.example/x"))
    finally:
        dialog.deleteLater()
        _collect_deleted()

    text = window.property("update_bar").text()
    assert "The link is: https://evil.example/x" in text
    assert "download page" not in text


def test_an_enormous_link_is_shortened_on_the_bar_but_whole_on_the_clipboard(
    window: Any, update_host: Any
) -> None:
    """A release body chooses this string, and the bar is one elided line.

    Shown short so the message stays readable; copied whole, because the
    clipboard is what the player actually uses (third cold review,
    2026-09-21).
    """
    long_url = "https://example.invalid/" + "a" * 900
    update_host.open_url = lambda url: False
    dialog = update_host.make_dialog(A_RELEASE)
    try:
        from PySide6.QtCore import QUrl

        dialog.notes.anchorClicked.emit(QUrl(long_url))
    finally:
        dialog.deleteLater()
        _collect_deleted()

    text = window.property("update_bar").text()
    assert len(text) < 400, f"the bar was handed {len(text)} characters"
    assert "…" in text
    assert text.startswith("Could not open a browser. The link is: https://example.invalid/")
    assert _clipboard().text() == long_url, "the clipboard keeps the whole vetted URL"


def test_an_absurd_link_is_refused_without_putting_it_anywhere(
    window: Any, update_host: Any
) -> None:
    """Past a few thousand characters it is not a link anyone is going to use."""
    absurd = "https://example.invalid/" + "b" * 5000
    _clipboard().setText("untouched")
    update_host.open_url = lambda url: False
    dialog = update_host.make_dialog(A_RELEASE)
    try:
        from PySide6.QtCore import QUrl

        dialog.notes.anchorClicked.emit(QUrl(absurd))
    finally:
        dialog.deleteLater()
        _collect_deleted()

    text = window.property("update_bar").text()
    assert "could not open the link" in text.lower()
    assert "b" * 100 not in text
    assert _clipboard().text() == "untouched", "an absurd URL was put on the clipboard"


def test_a_link_in_the_notes_falls_back_the_same_way(window: Any, update_host: Any) -> None:
    """The other route out of the dialog, through the host's REAL dialog factory.

    Not the fake: what this pins is that `make_dialog`'s default hands the
    notes view the host's opener, so both routes share one fallback.
    """
    update_host.open_url = lambda url: False
    dialog = update_host.make_dialog(A_RELEASE)
    try:
        from PySide6.QtCore import QUrl

        dialog.notes.anchorClicked.emit(QUrl("https://example.invalid/notes"))
    finally:
        dialog.deleteLater()
        # Or the next test's child count starts at one (the leak test found it).
        _collect_deleted()

    bar = window.property("update_bar")
    assert "https://example.invalid/notes" in bar.text()
    assert "Could not open a browser" in bar.text()
    assert _clipboard().text() == "https://example.invalid/notes"


def test_the_details_button_does_nothing_before_a_check_has_answered(
    window: Any, update_host: Any
) -> None:
    """The bar is hidden then, but a signal is not a guarantee about what raised it."""
    made: list[int] = []
    update_host.make_dialog = lambda result: made.append(1) or _FakeDialog(None)

    update_host.open_details()

    assert made == []


def _collect_deleted() -> None:
    """Deliver the `DeferredDelete` events `deleteLater()` posted.

    `processEvents()` does NOT deliver them, measured on this build: two
    widgets `deleteLater()`-ed and then pumped were both still children
    afterwards, and `sendPostedEvents(None, DeferredDelete)` took them. A test
    that pumped and then asserted "nothing was leaked" would be asserting about
    Qt's event filter, not about the code.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    process_events()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_every_dialog_is_handed_back_when_it_closes(window: Any, update_host: Any) -> None:
    """A real `UpdateDialog`, parented to the window, on the real path.

    Not the fake: the leak is Qt ownership, and a plain Python object cannot
    have it. `exec()` is the only thing overridden, because a modal one would
    block the run forever.
    """
    from yulon.ui.widgets.update_dialog import UpdateChoice, UpdateDialog

    class _NoBlockDialog(UpdateDialog):
        def exec(self) -> int:
            self.choice = UpdateChoice.LATER
            return 0

    update_host.startup_result(A_RELEASE)
    update_host.make_dialog = lambda result: _NoBlockDialog(result, parent=window)
    before = len(window.findChildren(UpdateDialog))

    for _ in range(2):
        window.property("update_bar").details_button.click()
        _collect_deleted()

    # A dialog left parented to the window means every click adds one.
    assert len(window.findChildren(UpdateDialog)) == before


def test_the_fake_dialog_is_handed_back_too(window: Any, update_host: Any) -> None:
    """The host calls `deleteLater()` on whatever the factory returned."""
    from yulon.ui.widgets.update_dialog import UpdateChoice

    update_host.startup_result(A_RELEASE)
    dialog = _FakeDialog(UpdateChoice.LATER)
    update_host.make_dialog = lambda result: dialog

    window.property("update_bar").details_button.click()

    assert dialog.deleted == 1


# ------------------------------------------------- one check at a time


class _HeldRunner:
    """A `JobRunner` that starts nothing and keeps the callbacks for the test to fire."""

    def __init__(self) -> None:
        self.work: list[Any] = []
        self.done: list[Any] = []
        self.failed: list[Any] = []

    def __call__(self, work: Any, on_done: Any, on_error: Any) -> None:
        self.work.append(work)
        self.done.append(on_done)
        self.failed.append(on_error)


def _check_button(window: Any) -> Any:
    from PySide6.QtWidgets import QPushButton

    button = window.findChild(QPushButton, "check-for-updates")
    assert button is not None
    return button


def test_a_second_click_while_a_check_is_running_starts_nothing(
    window: Any, update_host: Any
) -> None:
    """Two checks in flight are two writers of update.json, and one wasted request."""
    runner = _HeldRunner()
    update_host.run_job = runner
    update_host.check = lambda: A_RELEASE
    button = _check_button(window)

    button.click()
    assert button.isEnabled() is False, "the button stays pressable during a check"
    button.click()

    assert len(runner.work) == 1

    runner.done[0](A_RELEASE)
    assert button.isEnabled() is True, "the button never came back"


def test_the_slot_refuses_a_second_check_even_when_nothing_disabled_it(
    window: Any, update_host: Any
) -> None:
    """The flag, not the button.

    Disabling the button hides the second click from a USER, and a test that
    clicks twice cannot tell the two mechanisms apart — measured: removing the
    flag left all 51 tests green. `check_now` is a slot, and a slot is callable
    by anything that can reach the host.
    """
    runner = _HeldRunner()
    update_host.run_job = runner
    update_host.check = lambda: A_RELEASE

    update_host.check_now()
    _check_button(window).setEnabled(True)  # as if something re-enabled it
    update_host.check_now()

    assert len(runner.work) == 1


def test_a_check_that_fails_gives_the_button_back(window: Any, update_host: Any) -> None:
    """Both outcomes settle it; only one of them was the happy path."""
    runner = _HeldRunner()
    update_host.run_job = runner
    update_host.check = lambda: A_RELEASE
    button = _check_button(window)

    button.click()
    runner.failed[0](OSError("no route"))

    assert button.isEnabled() is True
    assert "no route" in window.property("update_bar").text()


def test_a_manual_check_while_the_launch_check_is_still_asking_is_refused(
    window: Any, update_host: Any
) -> None:
    """The same question is already in flight; a second one is a second writer."""

    class _Busy:
        def isRunning(self) -> bool:
            return True

    runner = _HeldRunner()
    update_host.run_job = runner
    update_host.check = lambda: A_RELEASE
    was, update_host.startup_thread = update_host.startup_thread, _Busy()
    try:
        _check_button(window).click()
    finally:
        update_host.startup_thread = was

    assert runner.work == []
    assert window.property("update_bar").text() == "Yu'lon is already checking for updates."
    assert _check_button(window).isEnabled() is True, "a refusal is not a check in flight"


def test_adopting_a_server_that_already_has_a_tab_rebuilds_it_for_the_new_distro(
    window: Any, tmp_path: Any
) -> None:
    """Otherwise every button on that tab keeps driving the LOCAL docker daemon.

    The tab was opened as an ordinary local install, so it was built with no
    distro; adopting the same server from WSL wrote the distro to `state.json`
    and stopped there, leaving the open tab wired to a daemon the server is not
    in. Nothing reports an error: Start finds nothing to start, Stop stops
    nothing, and Status says "not running" about a server that is - until the
    app is restarted.

    The whole services bundle has to be new, not just the `Controller`:
    `ControllerServices.for_wotlk()` bakes the distro into `DockerSql`,
    `DockerMysql` and the `Controller`, and captures it a fourth time in the
    `logs_source` lambda.
    """
    server_dir = tmp_path / "rebuild-me"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    stale = _tab_for(window, server_dir)
    assert stale.services.controller.wsl_distro is None

    catalog.adopted.emit("wow-wotlk", server_dir, None, "Ubuntu-24.04")

    live = _tab_for(window, server_dir)
    assert live is not stale, "the tab was patched in place instead of rebuilt"
    assert live.services is not stale.services
    assert live.services.controller.wsl_distro == "Ubuntu-24.04"
    assert stale not in window.yulon_controllers, "the torn-down tab is still in the shutdown list"
    tabs = window.property("tabs")
    assert tabs.indexOf(stale) == -1, "the stale tab is still in the tab bar"
    assert tabs.currentWidget() is live
    # The panel list is what `_stop_background_threads()` joins at exit. A stale
    # console panel left in it is a `wait()` on a widget nothing owns any more.
    assert stale.console_log not in window.yulon_log_panels


def test_re_adopting_a_server_from_the_same_distro_only_focuses_its_tab(
    window: Any, tmp_path: Any
) -> None:
    """Adopting twice is an ordinary thing to do, and must not cost the tab its state.

    A rebuild on every adopt would throw away whatever the console is following
    and whatever the Modules tab has loaded, and - if the old tab were ever left
    behind - give one server two tabs that disagree. The distro changing is the
    only thing that justifies one.
    """
    server_dir = tmp_path / "same-distro"
    catalog = _catalog_view(window)
    catalog.adopted.emit("wow-wotlk", server_dir, None, "Ubuntu-24.04")
    existing = _tab_for(window, server_dir)
    tabs = window.property("tabs")
    tabs.setCurrentIndex(0)  # so "it focused the tab" is a real change, not the status quo
    before = len(window.yulon_controllers)

    catalog.adopted.emit("wow-wotlk", server_dir, None, "Ubuntu-24.04")

    assert _tab_for(window, server_dir) is existing, "the tab was rebuilt for nothing"
    assert len(window.yulon_controllers) == before, "adopting twice made a second tab"
    assert tabs.currentWidget() is existing


def test_use_existing_on_a_wsl_server_does_not_demote_its_tab_to_the_local_daemon(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    r""" "I was not told a distro" is not the same fact as "this server is local".

    `installed` carries no distro, and "Use existing…" accepts a
    `\wsl.localhost\...` folder - which is exactly the spelling an adopted
    server is stored under, so the two paths collide on the same key. Reading
    that silence as a change rebuilt a working WSL tab against the local docker
    daemon: this feature's own failure mode, running backwards.

    Both halves are checked, because either alone leaves the server broken on
    the next launch: the live tab keeps its distro, and so does what is written
    to `state.json` - `remember()` REPLACES the entry for a game + dir, so an
    install rebuilt from the signal's own arguments erases what adoption learnt.
    """
    server_dir = tmp_path / "keep-my-distro"
    catalog = _catalog_view(window)
    catalog.adopted.emit("wow-wotlk", server_dir, None, "Ubuntu-24.04")
    existing = _tab_for(window, server_dir)

    catalog.installed.emit("wow-wotlk", server_dir, None)

    assert _tab_for(window, server_dir) is existing, "the WSL tab was rebuilt as a local one"
    assert existing.services.controller.wsl_distro == "Ubuntu-24.04"
    assert window.saved_states, "nothing was written back at all"
    remembered = window.saved_states[-1].find("wow-wotlk", server_dir)
    assert (
        remembered is not None and remembered.wsl_distro == "Ubuntu-24.04"
    ), "the distro was erased from what would be saved"


def test_a_tab_that_is_mid_import_is_not_torn_down_to_change_its_distro(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuild is a teardown, and one kind of work cannot survive one.

    The database import runs 10-30 minutes inside a blocking `subprocess.run`,
    so `shutdown()`'s join times out with the QThread still running; the
    deferred delete then destroys it, and a QThread destroyed while running
    ABORTS the process (0xC0000409) - here in a LIVE app rather than at exit.
    `busy_reason()` and the close guard already exist for exactly this, and the
    first version of the rebuild consulted neither (review, 2026-08-26).

    Refusing is the honest outcome: the import cannot be stopped, so the choice
    was only ever between waiting and a crash. The distro is already saved, so
    nothing is lost by the tab picking it up on the next start - and the user is
    told that, rather than left to find out.
    """
    from PySide6.QtWidgets import QMessageBox

    server_dir = tmp_path / "mid-import"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    busy = _tab_for(window, server_dir)

    monkeypatch.setattr(type(busy), "busy_reason", lambda _self: "The database import is running.")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: told.append(a[2]))

    catalog.adopted.emit("wow-wotlk", server_dir, None, "Ubuntu-24.04")

    assert _tab_for(window, server_dir) is busy, "a tab mid-import was torn down"
    assert told and "import" in told[0], f"the refusal was silent: {told}"
    assert "next time" in told[0], "the user was not told when it will take effect"


def test_two_installs_under_different_parents_do_not_get_the_same_tab_title(
    window: Any, tmp_path: Any
) -> None:
    """The tab strip was titled with the leaf folder alone, which is the one part that repeats.

    The installer suggests the same folder name to everybody, so a second
    server installed next to a first - a different disk, a different parent,
    the same suggested leaf - produced two tabs reading exactly the same thing.
    Nothing is lost (Stop is ownership-checked against the compose labels and
    refuses across installs), but the user cannot tell which tab drives which
    server, and both halves have to change: the older tab is just as wrong as
    the new one, so its title has to grow too.
    """
    first = tmp_path / "on-the-ssd" / "DadsMmoLab"
    second = tmp_path / "on-the-spinner" / "DadsMmoLab"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", first, None)
    catalog.installed.emit("wow-wotlk", second, None)

    tabs = window.property("tabs")
    titles = [tabs.tabText(tabs.indexOf(_tab_for(window, d))) for d in (first, second)]

    assert titles[0] != titles[1], f"both tabs read {titles[0]!r}"
    assert "on-the-ssd" in titles[0] and "on-the-spinner" in titles[1]
    # Distinguishing them must not mean printing the path: only the folders that
    # actually differ are added, so the tmp_path above them stays out of it.
    assert str(tmp_path) not in titles[0]


def test_a_controller_tab_carries_its_server_dir_as_a_tooltip(window: Any, tmp_path: Any) -> None:
    """The rail shows the short game name (T192) — the full server dir must stay
    reachable on hover, or the short title becomes information loss.

    T192 changed the tooltip from the bare path to "WoW WotLK — <path>", so the
    whole game name is on it too; the path is still all of it.
    """
    server_dir = tmp_path / "DadsMmoLab"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)

    tabs = window.property("tabs")
    index = tabs.indexOf(_tab_for(window, server_dir))
    assert tabs.tabToolTip(index) == f"WoW WotLK — {server_dir}"


def test_the_logs_tab_sits_under_the_catalog_and_stays_there(window: Any, tmp_path: Any) -> None:
    """T93: the owner's placement. A server tab is APPENDED, so it can never push Logs down."""
    from yulon.ui.logs_view import LogsView

    tabs = window.property("tabs")
    assert tabs.tabText(0) == "Catalog"
    assert isinstance(tabs.widget(1), LogsView) and tabs.tabText(1) == "Logs"
    assert tabs.widget(1) is window.yulon_logs_view
    server_dir = tmp_path / "logs-tab-order"
    _catalog_view(window).installed.emit("wow-wotlk", server_dir, None)
    assert isinstance(tabs.widget(1), LogsView), "a new server tab pushed the Logs tab down"
    assert tabs.indexOf(_tab_for(window, server_dir)) > 1


def test_a_support_file_being_saved_refuses_the_close(window: Any, monkeypatch: Any) -> None:
    """The join at exit waits 8 s and one docker read may take 50: a save must hold the close."""
    assert main._busy_reasons(window) == []
    monkeypatch.setattr(window.yulon_logs_view, "busy_reason", lambda: "saving the support file")
    assert main._busy_reasons(window) == ["saving the support file"]
    # The self-update asks `close_refusal()` as the guard does: it must hear the save too.
    assert main.close_refusal(window) == "saving the support file"


def test_a_tab_opened_after_startup_is_still_joined_when_the_window_closes(
    window: Any, tmp_path: Any
) -> None:
    """A running console on such a tab used to abort the process on close.

    `build_window()` handed its panel list to `setProperty()`, which stores a
    QVariant COPY: the exit path then walked the list as it stood when the
    window was built, and every tab opened during the session - each install,
    each adopt - was missing from it. So "Follow worldserver log" on a tab the
    user opened themselves left a QThread running while Qt was torn down, which
    aborts (0xC0000409) rather than warns.

    This one stops the window's threads itself, which is the behaviour under
    test, so it is deliberately LAST in the file: the window is shared, and
    nothing may run against it afterwards.
    """
    server_dir = tmp_path / "following-a-log"
    _catalog_view(window).adopted.emit("wow-wotlk", server_dir, None, "Ubuntu-24.04")
    view = _tab_for(window, server_dir)
    assert view in window.yulon_controllers, "a tab opened after startup is missing from the list"
    assert view.console_log in window.yulon_log_panels, "so is its console panel"

    def endless() -> Iterator[str]:
        while True:
            yield "worldserver line"
            time.sleep(0.005)

    # What "Follow worldserver log" leaves running, without a real `docker logs`.
    assert view.console_log.run(endless, title="worldserver log") is True
    process_events(50)
    assert view.console_log.running is True, "the job under test was not running to begin with"

    main._stop_background_threads(window)

    assert view.console_log.running is False, "the new tab's console thread outlived the window"


def test_the_entry_point_wires_installs_through_install_wiring_and_no_controller_package() -> None:
    """`main.py` must not carry its own copy of the probe wiring.

    Read by `ast` rather than by running `build_window()` (which needs Qt and a
    display): every import anywhere in the file — the nested ones inside
    `build_window()` included — is collected, and none may name a
    `controller_wow_wotlk` module. The one that wires an engine is
    `yulon.install_wiring`.

    The password check is case-insensitive on purpose: the copy this deletes
    spelled it `wotlk_modules.DEFAULT_DB_ROOT_PASSWORD`, which a case-sensitive
    `"db_root_password" not in source` walks straight past.
    """
    import ast

    source = Path(main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert not [m for m in modules if "controller_wow_wotlk" in m], sorted(modules)
    assert "yulon.install_wiring.installer_for_app" in modules
    assert "db_root_password" not in source.lower()
    assert "make_installer" not in source


# ----------------------------------------------------- 8.9a: the tab goes too
#
# The view signals the removal up; the window drops the tab and resets the
# Catalog tile. Both halves are here because the window owns both registries and
# the live `AppState`, and neither the view nor the catalog can reach them.


def test_a_finished_install_settles_the_new_tabs_channel(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`installed` -> the tab -> one `settle_channel_after_install()` (T87).

    The tab itself only checks on open; the window is the one thing that knows
    an install just finished, so it is the window that asks. A repeat install
    into a known folder focuses the existing tab, and that tab is asked.
    """
    from yulon.ui.controller_view import ControllerView

    asked: list[Any] = []
    monkeypatch.setattr(
        ControllerView,
        "settle_channel_after_install",
        lambda self: asked.append(self.services.controller.server_dir),
    )
    server_dir = tmp_path / "settle-me"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    assert asked == [], "`installed` alone is also what Use existing... emits; it must not settle"
    catalog.fresh_install.emit("wow-wotlk", server_dir, None)

    assert asked == [server_dir], asked
    assert _tab_for(window, server_dir) is not None


def test_use_existing_does_not_write_an_account_into_a_server_it_was_pointed_at(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pointing the app at a folder is not an install, and not permission to write (T87).

    `attach_existing()` emits `installed` and never `fresh_install`, so the
    window's settle stays out of it: a `settle()` on Idle would mint a GM
    account in an auth database the user only showed the app.
    """
    from yulon.ui.controller_view import ControllerView

    asked: list[Any] = []
    monkeypatch.setattr(
        ControllerView, "settle_channel_after_install", lambda self: asked.append(1)
    )
    server_dir = tmp_path / "pointed-at"
    catalog = _catalog_view(window)
    fired: list[str] = []
    catalog.fresh_install.connect(lambda *a: fired.append("fresh"))
    catalog.installed.emit("wow-wotlk", server_dir, None)

    assert asked == [] and fired == []
    assert _tab_for(window, server_dir) is not None


def test_an_uninstalled_server_loses_its_tab_and_every_registry_entry(
    window: Any, tmp_path: Any
) -> None:
    """`drop_controller()` and not a second teardown.

    A QThread destroyed while running ABORTS the process, which is why that
    function does `shutdown()`, the console panel's stop+join and the three
    registries before `removeTab`. An uninstall that removed the tab any other
    way would reintroduce exactly that abort, on top of a server that has just
    been deleted.
    """
    server_dir = tmp_path / "purge-me"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)
    tabs = window.property("tabs")
    assert tabs.indexOf(view) != -1

    view.uninstalled.emit("wow-wotlk", server_dir)

    assert view not in window.yulon_controllers, "the removed tab is still in the shutdown list"
    assert view.console_log not in window.yulon_log_panels, "its console panel is still joined"
    assert tabs.indexOf(view) == -1, "the tab is still in the tab bar"


def test_the_catalog_tile_is_recomputed_from_what_survived_the_uninstall(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recomputed from the surviving installs, never cleared.

    `installed_dirs()` is one folder per GAME, so a machine with two installs of
    one game still has one after the first is purged - and its tab is still
    open. The window is the only thing that knows what survived, so it is the
    window that hands the list over.
    """
    server_dir = tmp_path / "recompute-me"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)
    # The purge itself forgot the record (it is the last thing `run()` does);
    # this drives that seam directly, because the run is what a unit test must
    # not do.
    view.services.uninstall.forget()

    seen: list[Any] = []
    monkeypatch.setattr(catalog, "forget_installed", lambda g, s: seen.append((g, dict(s))))
    view.uninstalled.emit("wow-wotlk", server_dir)

    assert len(seen) == 1, seen
    game, surviving = seen[0]
    assert game == "wow-wotlk"
    assert server_dir not in surviving.values(), "the tile was recomputed from a stale record"


def test_the_tabs_uninstaller_forgets_the_windows_own_live_state(
    window: Any, tmp_path: Any
) -> None:
    """Not `load_state()` -> forget -> `save_state()`, which would clobber the session.

    `build_window()` holds ONE live `AppState` and every tab writes into it, so
    an uninstaller that re-read the file would forget this install and silently
    undo whatever else the session had remembered. The proof is object identity:
    the state saved by the forget is the same object the install was remembered
    into.
    """
    server_dir = tmp_path / "live-state"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)
    remembered = window.saved_states[-1]
    assert any(i.server_dir == server_dir for i in remembered.installs)

    view.services.uninstall.forget()

    saved = window.saved_states[-1]
    assert saved is remembered, "the uninstaller re-loaded state.json instead of using the live one"
    assert all(i.server_dir != server_dir for i in saved.installs)


def test_a_failing_save_restores_the_forgotten_record_in_the_live_state(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main.finish_removal()` (T95; `ControllerView.forget_install()` until then)
    catches `OSError` and keeps the tab open — a promise about the record that
    the live `AppState` broke the moment `state.forget()` ran, before the write
    that never landed (review, T34 round 2). Object identity, not a fresh load:
    the state this asserts on is the same one every other tab still writes into.
    """
    server_dir = tmp_path / "forget-restore-me"
    seen_states: list[Any] = []

    def _refuse(app_state: Any, path: Any = None) -> None:
        seen_states.append(app_state)
        raise PermissionError(13, "Access is denied", "state.json")

    monkeypatch.setattr(state, "save_state", _refuse)
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)

    with pytest.raises(OSError):
        view.services.uninstall.forget()

    live = seen_states[-1]
    assert live.find("wow-wotlk", server_dir) is not None, "the failed forget was not undone"
    # Mutation: drop the `state.remember(install)` restore in
    # `main._forget_live_record()`'s `except OSError` and this fails — the
    # live state stays forgotten even though nothing was ever written.


# --------------------------------------------- T95: removing a server from Yu'lon
#
# One path for the ×, the tab's right-click entry and the Server tab's button:
# `request_removal()` refuses while busy, asks once, stops a running server
# first, forgets through `_forget_live_record()` and drops the tab through
# `on_uninstalled()`. Driven through the widgets a user presses.


def _answer(monkeypatch: pytest.MonkeyPatch, *replies: bool) -> list[tuple[str, str, object]]:
    """Answer each `QMessageBox.question` in turn, as the plain int the static call gives (T33)."""
    from PySide6.QtWidgets import QMessageBox

    asked: list[tuple[str, str, object]] = []
    queue = list(replies)

    def question(
        _parent: object, title: str, text: str, _buttons: object = None, default: object = None
    ) -> int:
        asked.append((title, text, default))
        yes = queue.pop(0) if queue else False
        return int(QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No)

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def _told(monkeypatch: pytest.MonkeyPatch, kind: str) -> list[str]:
    from PySide6.QtWidgets import QMessageBox

    told: list[str] = []
    monkeypatch.setattr(QMessageBox, kind, lambda *a, **k: told.append(a[2]))
    return told


def _removable_tab(
    window: Any, monkeypatch: pytest.MonkeyPatch, server_dir: Path, game: str = "wow-tbc"
) -> tuple[Any, list[int]]:
    """A tab over a real folder, its jobs run inline, and its Stop recorded rather than run."""
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.widgets.job import run_inline

    # Read at view construction (`ControllerView.__init__`), so it must be set before the emit.
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "keep-me.txt").write_text("the player's own file\n", encoding="utf-8")
    _catalog_view(window).installed.emit(game, server_dir, None)
    view = _tab_for(window, server_dir)
    stops: list[int] = []

    def stop() -> bool:
        stops.append(1)
        return True

    view.services.controller.stop = stop

    def no_docker() -> Any:
        # The failed-stop path re-polls, as `_stop_failed()` does; the real
        # status would shell out to docker (`conftest.py`'s guard).
        raise RuntimeError("Docker is not asked in a unit test")

    view.services.controller.status = no_docker
    return view, stops


def _remembered(window: Any, game: str, server_dir: Path) -> bool:
    """The window's LIVE state: `saved_states[-1]` is the one `AppState` every tab writes into."""
    return window.saved_states[-1].find(game, server_dir) is not None


def test_the_server_tab_button_on_a_tbc_tab_asks_stops_forgets_and_keeps_every_file(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TBC has no Uninstall seam, so `services.uninstall.forget` never existed on it.

    The window's own `_forget_live_record()` is what forgets. The tab was never
    polled, so whether it runs is unknown, and unknown is stopped first.
    """
    from PySide6.QtWidgets import QMessageBox

    from yulon import forgetting

    asked = _answer(monkeypatch, True)
    server_dir = tmp_path / "t95-tbc"
    view, stops = _removable_tab(window, monkeypatch, server_dir)
    assert view.services.uninstall is None
    tabs = window.property("tabs")
    # What the Catalog's own install does before it emits `installed`; the
    # emit above is the window's half only.
    _catalog_view(window)._remember_installed("wow-tbc", server_dir)
    assert str(server_dir) in _catalog_view(window).button_for("wow-tbc").toolTip()
    saves_before = len(window.saved_states)

    view.forget_install_button.click()

    assert len(asked) == 1
    title, text, default = asked[0]
    assert title == forgetting.TITLE
    assert "Nothing is deleted" in text and "stopped first" in text
    assert default == QMessageBox.StandardButton.No, "the dialog must default to No"
    assert stops == [1], "a server that may be running was forgotten without a stop"
    assert tabs.indexOf(view) == -1
    assert view not in window.yulon_controllers
    assert not _remembered(window, "wow-tbc", server_dir)
    assert (server_dir / "keep-me.txt").is_file(), "removing from Yu'lon deleted a file"
    assert len(window.saved_states) > saves_before, "the forget was never written to state.json"
    tile = _catalog_view(window).button_for("wow-tbc")
    assert str(server_dir) not in tile.toolTip(), "the Catalog still names the removed server"
    survivors = window.saved_states[-1].installed_dirs()
    assert "wow-tbc" not in survivors, "the fixture remembers another TBC install"
    assert tile.text() == "Install" and tile.isEnabled(), "the tile was not handed back"


def test_answering_no_keeps_the_tab_the_record_and_the_server(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _answer(monkeypatch, False)
    server_dir = tmp_path / "t95-no"
    view, stops = _removable_tab(window, monkeypatch, server_dir)

    view.forget_install_button.click()

    assert len(asked) == 1
    assert stops == []
    assert window.property("tabs").indexOf(view) != -1
    assert _remembered(window, "wow-tbc", server_dir)
    # Mutation: compare the answer with `is StandardButton.Yes` in `request_removal()`
    # and the Yes test above fails: the fake returns a plain int (T33).


def test_a_server_the_last_poll_saw_stopped_is_still_stopped_before_it_is_forgotten(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex, final review (T95): a poll's "stopped" is trusted until the next poll begins.

    A server started outside Yu'lon after that poll and before the press was
    forgotten while it ran, with nothing left managing it. The stop now runs
    for every server whose folder exists, whatever the last poll said; the
    poll only chooses the dialog's words. `stop_staged()` is the ownership-
    checking stop, and it answers False when nothing was running.
    """
    from yulon.controller import InstallStatus

    asked = _answer(monkeypatch, True)
    server_dir = tmp_path / "t95-started-behind-our-back"
    view, stops = _removable_tab(window, monkeypatch, server_dir)
    view.services.controller.status = lambda: InstallStatus(db=False, auth=False, world=False)
    view.refresh_status()
    assert view.last_seen_running() is False
    remembered_at_the_stop: list[bool] = []

    def stop_what_was_started_outside() -> bool:
        stops.append(1)
        remembered_at_the_stop.append(_remembered(window, "wow-tbc", server_dir))
        return True  # something was running, and is down now

    view.services.controller.stop = stop_what_was_started_outside

    view.forget_install_button.click()

    assert "If it is running, it is stopped first" in asked[0][1]
    assert stops == [1], "a server the last poll saw stopped was forgotten without a stop"
    assert remembered_at_the_stop == [True], "it was forgotten before the stop"
    assert window.property("tabs").indexOf(view) == -1
    assert not _remembered(window, "wow-tbc", server_dir)


def test_a_stop_that_finds_nothing_running_goes_on_to_forget(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, True)
    server_dir = tmp_path / "t95-nothing-to-stop"
    view, stops = _removable_tab(window, monkeypatch, server_dir)

    def nothing_running() -> bool:
        stops.append(1)
        return False

    view.services.controller.stop = nothing_running

    view.forget_install_button.click()

    assert stops == [1]
    assert window.property("tabs").indexOf(view) == -1
    assert not _remembered(window, "wow-tbc", server_dir)


def test_a_server_the_last_poll_saw_running_is_told_it_is_running(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.controller import InstallStatus

    asked = _answer(monkeypatch, False)
    view, _ = _removable_tab(window, monkeypatch, tmp_path / "t95-running")
    view.services.controller.status = lambda: InstallStatus(db=True, auth=True, world=True)
    view.refresh_status()

    view.forget_install_button.click()

    assert "It is running, so it is stopped first" in asked[0][1]


def test_a_busy_tab_refuses_with_its_own_reason_and_asks_nothing(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _answer(monkeypatch, True)
    told = _told(monkeypatch, "information")
    view, stops = _removable_tab(window, monkeypatch, tmp_path / "t95-busy")
    monkeypatch.setattr(
        type(view), "busy_reason", lambda _self: "The database import is still running."
    )

    view.forget_install_button.click()

    assert told == ["The database import is still running."]
    assert asked == [] and stops == []
    assert window.property("tabs").indexOf(view) != -1


def test_a_failed_stop_asks_again_and_no_keeps_everything(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from yulon import docker, forgetting

    asked = _answer(monkeypatch, True, False)
    server_dir = tmp_path / "t95-stop-fails"
    view, _ = _removable_tab(window, monkeypatch, server_dir)

    def refuse() -> bool:
        raise docker.DockerCommandError("Docker would not say which project owns tbc-mangosd")

    view.services.controller.stop = refuse

    view.forget_install_button.click()

    assert [title for title, _, _ in asked] == [forgetting.TITLE, forgetting.STOP_FAILED_TITLE]
    assert "Docker would not say" in asked[1][1]
    assert asked[1][2] == QMessageBox.StandardButton.No
    assert window.property("tabs").indexOf(view) != -1
    assert _remembered(window, "wow-tbc", server_dir)
    assert "Docker would not say" in view.problem_label.text()


def test_a_failed_stop_overridden_by_a_second_yes_forgets_anyway(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import docker

    _answer(monkeypatch, True, True)
    server_dir = tmp_path / "t95-stop-fails-yes"
    view, _ = _removable_tab(window, monkeypatch, server_dir)

    def refuse() -> bool:
        raise docker.DockerCommandError("cannot stop")

    view.services.controller.stop = refuse

    view.forget_install_button.click()

    assert window.property("tabs").indexOf(view) == -1
    assert not _remembered(window, "wow-tbc", server_dir)


def test_a_job_started_during_the_stop_refuses_the_forget_after_it(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the Server buttons are locked while the removal's stop runs; a Restore is not.

    So the refusal is asked again when the stop answers, and a restore
    started in that gap keeps the tab, the record and the job's own thread.
    """
    from yulon import forgetting

    asked = _answer(monkeypatch, True)
    told = _told(monkeypatch, "information")
    server_dir = tmp_path / "t95-restore-in-the-gap"
    view, stops = _removable_tab(window, monkeypatch, server_dir)

    def stop_while_a_restore_starts() -> bool:
        stops.append(1)
        view._restore_running = True  # pressed on the Maintenance tab mid-stop
        return True

    view.services.controller.stop = stop_while_a_restore_starts

    view.forget_install_button.click()

    assert len(asked) == 1 and stops == [1]
    assert told == [forgetting.RESTORE_RUNNING]
    assert window.property("tabs").indexOf(view) != -1
    assert _remembered(window, "wow-tbc", server_dir)
    view._restore_running = False


def test_a_record_that_cannot_be_written_keeps_the_tab_and_the_live_record(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T34's OSError test, moved to the one path that now forgets."""
    _answer(monkeypatch, True)
    told = _told(monkeypatch, "warning")
    server_dir = tmp_path / "t95-unwritable"
    view, _ = _removable_tab(window, monkeypatch, server_dir)

    def _refuse(app_state: Any, path: Any = None) -> None:
        raise PermissionError(13, "Access is denied", "state.json")

    monkeypatch.setattr(state, "save_state", _refuse)

    view.forget_install_button.click()

    assert told and "Access is denied" in told[0]
    assert window.property("tabs").indexOf(view) != -1, "the tab went over a record still on disk"
    assert _remembered(window, "wow-tbc", server_dir), "the failed forget was not undone"


def test_a_gone_folder_is_forgotten_without_a_stop_and_with_t34s_words(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _answer(monkeypatch, True)
    server_dir = tmp_path / "t95-gone"
    view, stops = _removable_tab(window, monkeypatch, server_dir)
    shutil.rmtree(server_dir)

    view.forget_install_button.click()

    assert "no longer exists" in asked[0][1] and "NOT touched" in asked[0][1]
    assert stops == [], "a folder that is gone was stopped by a project-name guess (T34's rule)"
    assert window.property("tabs").indexOf(view) == -1


def _hover(bar: Any, index: int) -> None:
    """A real `QHoverEvent`, sent the way Qt sends one.

    Measured offscreen, an unshown window included.
    """
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QHoverEvent
    from PySide6.QtWidgets import QApplication

    centre = QPointF(bar.tabRect(index).center())
    QApplication.sendEvent(bar, QHoverEvent(QEvent.Type.HoverMove, centre, centre, QPointF(-1, -1)))


def _leave(bar: Any) -> None:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    QApplication.sendEvent(bar, QEvent(QEvent.Type.Leave))


def _strip_of(window: Any, view: Any) -> Any:
    """The tab's button strip: ▶ and × side by side (T187), where T95's × alone sat."""
    from PySide6.QtWidgets import QTabBar

    tabs = window.property("tabs")
    return tabs.tabBar().tabButton(tabs.indexOf(view), QTabBar.ButtonPosition.RightSide)


def _x_of(window: Any, view: Any) -> Any:
    from PySide6.QtWidgets import QToolButton

    from yulon.ui.theme import FORGET_TAB_BUTTON

    strip = _strip_of(window, view)
    return None if strip is None else strip.findChild(QToolButton, FORGET_TAB_BUTTON)


def _play_of(window: Any, view: Any) -> Any:
    from PySide6.QtWidgets import QToolButton

    from yulon.ui.theme import LAUNCH_TAB_BUTTON

    strip = _strip_of(window, view)
    return None if strip is None else strip.findChild(QToolButton, LAUNCH_TAB_BUTTON)


def test_only_server_tabs_carry_an_x(window: Any, tmp_path: Any) -> None:
    """Decided by page type, not index: Catalog is 0 and T93 puts Logs at 1."""
    from PySide6.QtWidgets import QTabBar, QToolButton

    from yulon.ui.controller_view import ControllerView
    from yulon.ui.theme import FORGET_TAB_BUTTON

    _catalog_view(window).installed.emit("wow-wotlk", tmp_path / "t95-has-x", None)
    tabs = window.property("tabs")
    bar = tabs.tabBar()
    for index in range(tabs.count()):
        page = tabs.widget(index)
        right = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
        left = bar.tabButton(index, QTabBar.ButtonPosition.LeftSide)
        assert left is None
        if isinstance(page, ControllerView):
            assert right is not None
            assert right.findChild(QToolButton, FORGET_TAB_BUTTON) is not None
        else:
            assert right is None, f"{tabs.tabText(index)!r} has an ×"


def test_the_x_shows_on_the_hovered_tab_and_the_current_one_and_nowhere_else(
    window: Any, tmp_path: Any
) -> None:
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", tmp_path / "t95-first", None)
    catalog.installed.emit("wow-wotlk", tmp_path / "t95-second", None)
    first, second = _tab_for(window, tmp_path / "t95-first"), _tab_for(
        window, tmp_path / "t95-second"
    )
    tabs = window.property("tabs")
    bar = tabs.tabBar()
    x_first, x_second = _x_of(window, first), _x_of(window, second)
    assert tabs.currentWidget() is second

    assert x_first.isHidden() and not x_second.isHidden(), "only the current tab's × at rest"
    _hover(bar, tabs.indexOf(first))
    assert not x_first.isHidden() and not x_second.isHidden(), "hover shows it, current keeps it"
    _leave(bar)
    assert x_first.isHidden() and not x_second.isHidden()
    tabs.setCurrentWidget(first)
    assert not x_first.isHidden() and x_second.isHidden(), "the × follows the current tab"
    shown = [
        index
        for index in range(tabs.count())
        if (x := _x_of(window, tabs.widget(index))) is not None and not x.isHidden()
    ]
    assert shown == [tabs.indexOf(first)], "an × is showing on a tab nobody is on or over"


def test_the_x_is_not_a_gamepad_stop_and_is_exempt_from_the_touch_floor(
    window: Any, tmp_path: Any
) -> None:
    from PySide6.QtCore import Qt

    _catalog_view(window).installed.emit("wow-wotlk", tmp_path / "t95-size", None)
    x = _x_of(window, _tab_for(window, tmp_path / "t95-size"))
    x.ensurePolished()
    assert x.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert x.maximumWidth() <= 18 and x.maximumHeight() <= 18, x.maximumSize()


def test_pressing_the_x_is_the_same_removal(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import forgetting

    asked = _answer(monkeypatch, True)
    view, stops = _removable_tab(window, monkeypatch, tmp_path / "t95-x-press")

    _x_of(window, view).click()

    assert [title for title, _, _ in asked] == [forgetting.TITLE]
    assert stops == [1]
    assert window.property("tabs").indexOf(view) == -1


def test_the_tab_menu_offers_the_same_removal_on_server_tabs_only(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven through the menu's builder, the way `test_controller_view.py`'s `_row_menu()` is.

    `QMenu.exec` cannot be replaced from Python (a Shiboken slot: the patch is
    accepted and ignored), and a popup driven from inside its own loop is
    unbounded if anything runs that loop first. So the right-click's menu is
    built by `window.yulon_tab_menu(pos)` and shown by a two-line caller, and
    the entry is triggered here, with no popup open, as Qt triggers one after
    the menu has closed.
    """
    from yulon import forgetting

    asked = _answer(monkeypatch, False)
    view, _ = _removable_tab(window, monkeypatch, tmp_path / "t95-menu")
    tabs = window.property("tabs")
    bar = tabs.tabBar()

    menu = window.yulon_tab_menu(bar.tabRect(tabs.indexOf(view)).center())
    entries = {action.text(): action for action in menu.actions()}
    assert "Copy Server Path" in entries, "not this tab's menu"
    assert forgetting.BUTTON_LABEL in entries
    entries[forgetting.BUTTON_LABEL].trigger()
    assert [title for title, _, _ in asked] == [forgetting.TITLE], "not the same dialog"
    assert tabs.indexOf(view) != -1, "answered No, yet the tab went"

    # T192: the Catalog is pinned above the rail, no longer a tab in it, so
    # there is nothing of it in the bar to right-click (its menu offered one
    # disabled caption and nothing else).
    assert not bar.isTabVisible(0) and bar.tabRect(0).isEmpty()
    # And should a point ever answer the Catalog's index, it has no menu.
    from PySide6.QtCore import QPoint

    monkeypatch.setattr(bar, "tabAt", lambda _pos: 0)
    assert window.yulon_tab_menu(QPoint(1, 1)) is None, "the Catalog has a bar menu"


def test_the_tab_menu_answered_yes_removes_the_server(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import forgetting

    asked = _answer(monkeypatch, True)
    view, stops = _removable_tab(window, monkeypatch, tmp_path / "t95-menu-yes")
    tabs = window.property("tabs")
    menu = window.yulon_tab_menu(tabs.tabBar().tabRect(tabs.indexOf(view)).center())

    next(a for a in menu.actions() if a.text() == forgetting.BUTTON_LABEL).trigger()

    assert [title for title, _, _ in asked] == [forgetting.TITLE]
    assert stops == [1]
    assert tabs.indexOf(view) == -1


def test_a_right_click_off_every_tab_builds_no_menu(window: Any) -> None:
    from PySide6.QtCore import QPoint

    assert window.yulon_tab_menu(QPoint(-50, -50)) is None


# ------------------------------------------------ T36: the client-folder seam


def test_the_client_dir_seam_replaces_the_record_and_keeps_server_dir_and_distro(
    window: Any, tmp_path: Any
) -> None:
    """`main._remember_client_live()`'s live-`AppState` write, over an adopted install.

    An adopted server carries a `wsl_distro` the client-folder press never
    touches, and `KnownInstall` is a frozen pydantic model rather than a
    stdlib dataclass — `model_copy(update=...)`, not `dataclasses.replace()` —
    so this is also where a rewrite that forgot the copy and rebuilt a bare
    `KnownInstall` from scratch would show up: the distro would vanish.
    """
    server_dir = tmp_path / "client-dir-live"
    catalog = _catalog_view(window)
    catalog.adopted.emit("wow-wotlk", server_dir, None, "Ubuntu-24.04")
    view = _tab_for(window, server_dir)
    before = window.saved_states[-1].find("wow-wotlk", server_dir)
    assert before is not None and before.wsl_distro == "Ubuntu-24.04"

    client = tmp_path / "TurtleWoW"
    view.services.set_client_dir(client)

    after = window.saved_states[-1].find("wow-wotlk", server_dir)
    assert after is not None
    assert after.client_dir == client
    assert after.server_dir == server_dir
    assert after.wsl_distro == "Ubuntu-24.04", "the distro was dropped by the client-folder write"
    # Mutation: in `main._remember_client_live()`, replace
    # `install.model_copy(update={"client_dir": client_dir})` with a freshly
    # built `KnownInstall(game=game, server_dir=server_dir,
    # client_dir=client_dir)` — `after.wsl_distro` reads `None` and this fails.


def test_a_failing_client_dir_save_restores_the_old_record(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The T34-round-2 pattern applied to the new seam: nothing durable from a failed write."""
    server_dir = tmp_path / "client-dir-restore"
    seen_states: list[Any] = []

    def _refuse(app_state: Any, path: Any = None) -> None:
        seen_states.append(app_state)
        raise PermissionError(13, "Access is denied", "state.json")

    monkeypatch.setattr(state, "save_state", _refuse)
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)

    with pytest.raises(OSError):
        view.services.set_client_dir(tmp_path / "TurtleWoW")

    live = seen_states[-1]
    restored = live.find("wow-wotlk", server_dir)
    assert restored is not None
    assert restored.client_dir is None, "the failed write was not undone"
    # Mutation: drop the `state.remember(install)` restore in the `except
    # OSError` branch of `main._remember_client_live()` — `restored.client_dir`
    # would read the new folder even though `save_state()` never succeeded.


def test_the_client_dir_seam_rebuilds_the_tab_so_the_new_folder_reaches_the_applier(
    window: Any, tmp_path: Any
) -> None:
    """T36 DoD 5, end to end: the folder is baked into the applier at construction.

    `test_a_client_folder_with_no_interface_directory_is_not_written_into`
    (`test_controller_view.py`) already pins the factory alone; this proves
    the loop the row actually drives — write, then `client_dir_changed`,
    then a rebuilt tab whose applier reflects the new folder — through the
    real signal wiring in `main.py`, on `wow-tortoise`, the one tree whose
    module applier goes through `_client_dir_for_addons()`.
    """
    server_dir = tmp_path / "tw-client-change"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-tortoise", server_dir, None)
    view = _tab_for(window, server_dir)
    assert view.services.applier is not None
    assert view.services.applier.client_dir is None, "a None client dir must refuse the addon step"

    client = tmp_path / "TurtleWoW"
    (client / "Interface").mkdir(parents=True)
    assert view.services.set_client_dir is not None
    view.services.set_client_dir(client)
    view.client_dir_changed.emit("wow-tortoise", server_dir, client)

    rebuilt = _tab_for(window, server_dir)
    assert rebuilt is not view, "the old tab was patched in place instead of rebuilt"
    assert rebuilt.services.applier is not None
    assert rebuilt.services.applier.client_dir == client
    # Mutation: in `main.on_client_dir_changed()`, call `add_controller(game,
    # sd, None, ...)` instead of `add_controller(game, sd, cd, ...)` —
    # `rebuilt.services.applier.client_dir` reads `None` and this fails.


# ------------------------------------------------- the update that installs itself


class _Busy:
    """A controller view that is in the middle of something that cannot be stopped."""

    def __init__(self, reason: str | None) -> None:
        self._reason = reason

    def busy_reason(self) -> str | None:
        return self._reason


class _Window:
    """Only what `close_refusal()` reads off a window."""

    def __init__(self, *views: Any) -> None:
        self.yulon_controllers = list(views)


def test_close_refusal_is_silent_when_nothing_is_running() -> None:
    assert main.close_refusal(_Window()) is None
    assert main.close_refusal(_Window(_Busy(None), _Busy(None))) is None


def test_close_refusal_answers_the_first_reason_it_finds() -> None:
    assert main.close_refusal(_Window(_Busy(None), _Busy("A database import is running."))) == (
        "A database import is running."
    )


def test_close_refusal_ignores_a_view_that_has_no_busy_reason_at_all() -> None:
    """Older tabs and anything a test puts in the list; the sweep is defensive on purpose."""
    assert main.close_refusal(_Window(object(), _Busy("An import is running."))) == (
        "An import is running."
    )


def test_close_refusal_on_a_window_with_no_controllers_at_all() -> None:
    assert main.close_refusal(object()) is None


class _Bar:
    """Only what `announce_previous_update()` says to."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.fading: list[str] = []

    def show_message(self, text: str, *, keep_details: bool = False, fade: bool = False) -> None:
        self.messages.append(text)
        if fade:
            self.fading.append(text)


def _swapped_install(tmp_path: Path) -> Any:
    """A folder install with a MARKED `.yulon-old` inside it, as the helper leaves things."""
    import os

    from yulon.selfupdate import layout
    from yulon.selfupdate.detect import Install, InstallKind

    target = tmp_path / "app"
    (target / "_internal").mkdir(parents=True)
    (target / "yulon").write_text("the new build", encoding="utf-8")
    install = Install(InstallKind.TARBALL, target, "yulon", True)
    old = layout.work_dir(install, layout.OLD_NAME)
    old.mkdir()
    (old / "yulon").write_text("the previous build", encoding="utf-8")
    layout.write_marker(
        install,
        layout.OLD_NAME,
        layout.Marker(
            layout.OLD_NAME,
            "v1",
            "v2",
            os.getpid(),
            layout.now(),
            entries=("yulon", "_internal"),
            state=layout.SWAPPING,
        ),
    )
    return install


def test_the_first_start_after_a_swap_removes_the_old_build_and_says_so(tmp_path: Path) -> None:
    bar = _Bar()
    install = _swapped_install(tmp_path)

    assert main.announce_previous_update(bar, install=install, environ={}, version="v2") is True

    assert not (tmp_path / "app" / ".yulon-old").exists()
    assert bar.messages == ["Updated to Yu'lon 2."]
    # T179 fix round 2: it clears itself, so a notice waiting behind it gets its turn.
    assert bar.fading == ["Updated to Yu'lon 2."]


def test_an_ordinary_start_removes_nothing_and_says_nothing(tmp_path: Path) -> None:
    from yulon.selfupdate.detect import Install, InstallKind

    target = tmp_path / "app"
    target.mkdir()
    bar = _Bar()
    install = Install(InstallKind.TARBALL, target, "yulon", True)

    assert main.announce_previous_update(bar, install=install, environ={}) is False
    assert bar.messages == []


def test_a_start_that_cannot_remove_the_old_build_still_starts(tmp_path: Path) -> None:
    """It runs while the window is built; a traceback here is a launcher that will not open."""
    bar = _Bar()
    install = _swapped_install(tmp_path)
    assert install.target is not None
    install.target.chmod(0o500)
    try:
        assert main.announce_previous_update(bar, install=install, environ={}) is False
        # It still SPEAKS: the marker says the update was to a version this is
        # not, so the honest answer is that it did not happen. What must not
        # happen is a traceback out of `build_window()`.
        assert bar.messages and "is not installed" in bar.messages[0]
    finally:
        install.target.chmod(0o700)


def test_the_downloads_folder_is_one_the_player_can_find(monkeypatch: pytest.MonkeyPatch) -> None:
    """`~/Downloads` when it is there, the home folder when it is not."""
    import pathlib

    home = Path(os.environ.get("HOME", "/"))
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: home))
    answer = main.downloads_dir()
    assert answer in (home / "Downloads", home)
    assert answer.is_dir(), "the folder a verified download is told to go to does not exist"


class _FakeProgress:
    """`UpdateProgressDialog` with no window: the four things the host uses."""

    def __init__(self) -> None:
        import threading

        from yulon.ui.widgets.update_progress import _Relay

        self.cancel_event = threading.Event()
        self.relay = _Relay()
        self.shown = 0
        self.accepted = 0
        self.rejected = 0
        self.closed = 0
        self.errors: list[str] = []

    def show(self) -> None:
        self.shown += 1

    def accept(self) -> None:
        self.accepted += 1

    def reject(self) -> None:
        self.rejected += 1

    def close_now(self) -> None:
        self.closed += 1

    def finish_error(self, message: str) -> None:
        self.errors.append(message)


def _swappable(tmp_path: Path) -> Any:
    from yulon.selfupdate.detect import Install, InstallKind

    target = tmp_path / "yulon"
    target.mkdir(exist_ok=True)
    return Install(InstallKind.TARBALL, target, "yulon", True)


def _install_offer() -> Any:
    """A release this machine could really install: checksums, and its own artifact."""
    return dataclasses.replace(
        A_RELEASE,
        assets=(
            update.ReleaseAsset(
                "Yulon-v0.8.70-Public-x86_64.tar.gz", "https://example.invalid/a", 1234
            ),
            update.ReleaseAsset("SHA256SUMS", "https://example.invalid/s", 10),
        ),
        has_checksums=True,
    )


@pytest.fixture
def installing_host(update_host: Any, tmp_path: Path) -> Iterator[Any]:
    """`update_host` with the install-side seams replaced, and put back afterwards."""
    from yulon.ui.widgets.job import run_inline

    names = (
        "current_install",
        "other_copies",
        "await_helper",
        "apply",
        "start_helper",
        "end_helper",
        "release_after",
        "discard_script",
        "make_way",
        "close_window",
        "refusal",
        "make_progress",
        "run_job",
    )
    seams = {name: getattr(update_host, name) for name in names}
    update_host.current_install = lambda: _swappable(tmp_path)
    update_host.other_copies = lambda _exe: []
    update_host.refusal = lambda: None
    update_host.run_job = run_inline
    update_host.progresses = []

    def make_progress(_version: str) -> Any:
        progress = _FakeProgress()
        update_host.progresses.append(progress)
        return progress

    update_host.make_progress = make_progress
    update_host.helpers = []

    class _Handle:
        """What `start_helper` really returns: a process that is still running."""

        def poll(self) -> int | None:
            return None

    def started(plan: Any) -> Any:
        update_host.helpers.append(plan)
        return _Handle()

    update_host.start_helper = started
    update_host.ended = []

    def ended(handle: Any) -> bool:
        update_host.ended.append(handle)
        return True  # it stopped; the tests about it NOT stopping say so

    update_host.end_helper = ended
    update_host.released = []
    update_host.release_after = lambda install, plan: update_host.released.append(plan)
    update_host.discarded = []
    update_host.discard_script = update_host.discarded.append
    update_host.make_way = lambda _install, **_kw: None  # nothing is holding the lock
    update_host.closes = []
    update_host.close_window = lambda: update_host.closes.append(1)
    # The helper's "I am running" stamp, holding THIS attempt's nonce. Tests
    # about anything ELSE say yes here; the ones about the stamp replace this.
    update_host.await_helper = lambda _stamp, _nonce: True
    yield update_host
    for name, seam in seams.items():
        setattr(update_host, name, seam)
    update_host._installing = False
    update_host._restarting = False
    update_host._progress = None
    update_host._ready = None
    update_host._helper = None


def test_a_ready_update_starts_the_helper_and_then_closes_the_window(
    installing_host: Any, tmp_path: Path
) -> None:
    """**The order is the whole of it.** The helper waits for THIS pid to exit."""
    from yulon.selfupdate.apply import ReadyToRestart

    order: list[str] = []
    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    started: list[Any] = []

    def start(p: Any) -> Any:
        started.append(p)
        order.append("helper")
        return None

    installing_host.start_helper = start
    installing_host.close_window = lambda: order.append("close")

    installing_host.start_update(_install_offer())

    assert order == ["helper", "close"]
    assert started[0].nonce and started[0].script.exists(), "the helper was not armed"
    assert started[0].script.name.startswith(f"yulon-update-{os.getpid()}-")
    assert installing_host.progresses[0].shown == 1
    assert installing_host.progresses[0].closed == 1


def test_the_helper_is_never_started_while_a_tab_refuses_to_close(
    installing_host: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An update ends in `window.close()`; starting one mid-import is a way past the guard.

    The refusal is the SAME `close_refusal()` the window's close filter asks,
    and the assertion is that `apply` was never called — not merely that a
    message was shown.
    """
    from PySide6.QtWidgets import QMessageBox

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda _p, title, text, *a, **k: said.append((title, text)) or 0),
    )
    applied: list[int] = []
    installing_host.apply = lambda *a, **k: applied.append(1)
    installing_host.refusal = lambda: "A database import is running."

    installing_host.start_update(_install_offer())

    assert applied == [], "the update ran while a tab refused to close"
    assert installing_host.helpers == [] and installing_host.closes == []
    assert installing_host.progresses == [], "a progress dialog was opened for a refused update"
    assert said and "A database import is running." in said[0][1]
    assert said[0][1].startswith("Yu'lon can update when this is finished:")


def test_a_refused_update_shows_the_reason_and_leaves_the_app_running(
    installing_host: Any,
) -> None:
    from yulon.selfupdate.fetch import UpdateError

    def refuse(*_a: Any, **_k: Any) -> Any:
        raise UpdateError("The download's checksum does not match. Nothing was installed.")

    installing_host.apply = refuse

    installing_host.start_update(_install_offer())

    progress = installing_host.progresses[0]
    assert progress.errors == ["The download's checksum does not match. Nothing was installed."]
    assert progress.closed == 0, "a dialog showing a refusal must stay up to be read"
    assert installing_host.helpers == [] and installing_host.closes == []


def test_a_cancelled_update_just_closes_the_progress_dialog(installing_host: Any) -> None:
    from yulon.selfupdate.fetch import Cancelled

    def cancel(*_a: Any, **_k: Any) -> Any:
        raise Cancelled("The update was cancelled.")

    installing_host.apply = cancel

    installing_host.start_update(_install_offer())

    progress = installing_host.progresses[0]
    assert (progress.closed, progress.errors) == (1, [])
    assert installing_host.helpers == [] and installing_host.closes == []


def test_a_saved_download_is_shown_to_the_player_and_nothing_is_replaced(
    installing_host: Any, tmp_path: Path
) -> None:
    from yulon.selfupdate.apply import SavedForManualInstall

    saved = tmp_path / "Downloads" / "Yulon-v0.8.70-Public-macos.dmg"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(b"x")
    installing_host.apply = lambda *a, **k: SavedForManualInstall(saved, "v0.8.70-Public")
    opened: list[str] = []
    installing_host.open_url = _opens_into(opened)

    installing_host.start_update(_install_offer())

    bar = installing_host.parent().property("update_bar")
    assert str(saved) in bar.text()
    assert "Close Yu'lon, then install it." in bar.text()
    assert opened and opened[0].startswith("file://") and opened[0].endswith(saved.name)
    assert installing_host.helpers == [] and installing_host.closes == []


def test_the_worker_is_handed_this_process_and_the_dialogs_own_cancel(
    installing_host: Any,
) -> None:
    """What `apply_update` is called with, which is what a fake can never notice by itself."""
    from yulon.selfupdate.apply import ReadyToRestart
    from yulon.selfupdate.swap import SwapPlan

    seen: dict[str, Any] = {}

    def record(result: Any, install: Any, **kwargs: Any) -> Any:
        seen["result"] = result
        seen["install"] = install
        seen.update(kwargs)
        return ReadyToRestart(SwapPlan(Path("x"), ["/bin/sh"]), "v0.8.70-Public")

    installing_host.apply = record
    offer = _install_offer()

    installing_host.start_update(offer)

    assert seen["result"] is offer
    assert seen["pid"] == os.getpid()
    progress = installing_host.progresses[0]
    assert seen["cancelled"]() is False
    progress.cancel_event.set()
    assert seen["cancelled"]() is True, "the dialog's Cancel is not what the worker reads"
    assert seen["progress"] == progress.relay.emit_progress
    assert seen["stage_changed"] == progress.relay.emit_stage


def test_an_install_this_app_cannot_replace_opens_the_release_page_instead(
    installing_host: Any,
) -> None:
    """A checkout, an unsupported machine, or a release with no checksums."""
    from yulon.selfupdate.detect import Install, InstallKind

    installing_host.current_install = lambda: Install(InstallKind.SOURCE, None, "", False)
    applied: list[int] = []
    installing_host.apply = lambda *a, **k: applied.append(1)
    opened: list[str] = []
    installing_host.open_url = _opens_into(opened)

    installing_host.start_update(_install_offer())

    assert applied == []
    assert opened == [A_RELEASE.url]
    assert installing_host.progresses == []


def test_a_second_press_while_an_update_is_running_does_nothing(installing_host: Any) -> None:
    """The guard the check already has, for the action that ends in closing the window."""
    from yulon.selfupdate.apply import ReadyToRestart
    from yulon.selfupdate.swap import SwapPlan

    started: list[int] = []

    def apply_and_stay_running(*_a: Any, **_k: Any) -> Any:
        started.append(1)
        return ReadyToRestart(SwapPlan(Path("x"), ["/bin/sh"]), "v0.8.70-Public")

    installing_host.apply = apply_and_stay_running
    installing_host._installing = True

    installing_host.start_update(_install_offer())

    assert started == [], "a second update started while one was in flight"


def test_the_dialogs_action_button_says_what_pressing_it_will_do(
    update_host: Any, tmp_path: Path
) -> None:
    """The label and the behaviour come from ONE call, so they cannot disagree.

    Driven through the REAL `make_dialog`, which is the only thing that joins
    them: a test that called `action_label()` itself would prove the function
    and not the wiring.
    """
    from yulon.selfupdate.detect import Install, InstallKind
    from yulon.ui.widgets.update_dialog import UpdateChoice

    offer = _install_offer()
    update_host._offered = offer

    update_host.current_install = lambda: _swappable(tmp_path)
    dialog = update_host.make_dialog(offer)
    try:
        assert dialog._buttons[UpdateChoice.UPDATE].text() == "Update now"
    finally:
        dialog.deleteLater()

    update_host.current_install = lambda: Install(InstallKind.MACOS_APP, None, "", False)
    mac_offer = dataclasses.replace(
        offer,
        assets=(
            update.ReleaseAsset("Yulon-v0.8.70-Public-macos.dmg", "https://example.invalid/d", 5),
            update.ReleaseAsset("SHA256SUMS", "https://example.invalid/s", 10),
        ),
    )
    dialog = update_host.make_dialog(mac_offer)
    try:
        assert dialog._buttons[UpdateChoice.UPDATE].text() == "Download"
    finally:
        dialog.deleteLater()

    update_host.current_install = lambda: Install(InstallKind.SOURCE, None, "", False)
    dialog = update_host.make_dialog(offer)
    try:
        assert dialog._buttons[UpdateChoice.UPDATE].text() == "Open download page"
    finally:
        dialog.deleteLater()


# ------------------------------------- the update's second look at the close gate


def test_the_close_gate_is_asked_AGAIN_immediately_before_the_helper_starts(
    installing_host: Any, tmp_path: Path
) -> None:
    """**The first ask says nothing about now** (cold review 1).

    The download, the unpack and the smoke test take minutes, and a player can
    start a database import in them. Starting the helper anyway would close the
    window past the guard that exists to stop exactly that — or, if the close
    were refused, leave a helper counting down beside a running import.

    The refusal here appears only AFTER the update has been allowed to begin,
    which is the case a single ask cannot see.
    """
    from yulon.selfupdate.apply import ReadyToRestart
    from yulon.selfupdate.swap import SwapPlan

    asked: list[int] = []

    def refusal() -> str | None:
        asked.append(1)
        return None if len(asked) == 1 else "A database import is running."

    installing_host.refusal = refusal
    installing_host.apply = lambda *a, **k: ReadyToRestart(
        SwapPlan(tmp_path / "h.sh", ["/bin/sh"]), "v0.8.70-Public"
    )

    installing_host.start_update(_install_offer())

    assert len(asked) == 2, "the gate was asked once, not again before the helper"
    assert installing_host.helpers == [], "the helper was started during an import"
    assert installing_host.closes == [], "the window was closed past the guard"
    bar = installing_host.parent().property("update_bar")
    assert "ready" in bar.text() and "A database import is running." in bar.text()
    assert "press Update now again" in bar.text()


def test_a_second_copy_of_yulon_stops_the_swap(installing_host: Any, tmp_path: Path) -> None:
    """Two copies open is how an install gets lost: the helper swaps under the other one."""
    from yulon.selfupdate.apply import ReadyToRestart
    from yulon.selfupdate.swap import SwapPlan

    installing_host.apply = lambda *a, **k: ReadyToRestart(
        SwapPlan(tmp_path / "h.sh", ["/bin/sh"]), "v0.8.70-Public"
    )
    installing_host.other_copies = lambda _exe: [99999]

    installing_host.start_update(_install_offer())

    assert installing_host.helpers == [] and installing_host.closes == []
    bar = installing_host.parent().property("update_bar")
    assert "another copy of Yu'lon is open" in bar.text()


def test_the_other_copies_seam_is_asked_about_the_install_s_own_executable(
    installing_host: Any, tmp_path: Path
) -> None:
    from yulon.selfupdate.apply import ReadyToRestart
    from yulon.selfupdate.swap import SwapPlan

    asked: list[Path] = []
    installing_host.apply = lambda *a, **k: ReadyToRestart(
        SwapPlan(tmp_path / "h.sh", ["/bin/sh"]), "v0.8.70-Public"
    )
    installing_host.other_copies = lambda exe: asked.append(exe) or []

    installing_host.start_update(_install_offer())

    assert asked == [tmp_path / "yulon" / "yulon"]


def test_a_cancel_closes_the_progress_dialog_for_real(installing_host: Any) -> None:
    """`close_now()` and not `reject()`: on this dialog `reject()` MEANS cancel."""
    from yulon.selfupdate.fetch import Cancelled

    def cancel(*_a: Any, **_k: Any) -> Any:
        raise Cancelled("The update was cancelled.")

    installing_host.apply = cancel
    installing_host.start_update(_install_offer())

    progress = installing_host.progresses[0]
    assert progress.closed == 1 and progress.errors == []


# ------------------------------------------------ what the smoke run must not do


def test_the_smoke_run_is_recognised_by_its_variable_and_nothing_else() -> None:
    assert main.in_smoke_test({"YULON_SMOKE_TEST": "1"}) is True
    assert main.in_smoke_test({}) is False
    assert main.in_smoke_test({"YULON_SMOKE_TEST": ""}) is False


def test_the_smoke_run_does_not_repair_the_players_state_file(tmp_path: Path) -> None:
    """A staged build proving it opens must not move the INSTALLED build's files aside.

    `load_state()` moves an unreadable `state.json` to `.broken`, which is the
    right thing for a real start and the wrong thing for a build that is not
    installed yet.
    """
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    assert _REAL_LOAD_STATE(path, repair=False).installs == []
    assert path.read_text(encoding="utf-8") == "{not json", "the smoke run moved it aside"
    assert not (tmp_path / "state.json.broken").exists()

    assert _REAL_LOAD_STATE(path, repair=True).installs == []
    assert (tmp_path / "state.json.broken").exists(), "a real start still repairs it"


def test_the_window_built_by_the_smoke_run_starts_no_update_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No GitHub request and no `update.json`, from a build that is not installed yet.

    Built through the real `build_window()` under the variable, with the check
    replaced by something that records: what is asserted is that it was never
    called and that no update thread exists to call it.
    """
    from PySide6.QtWidgets import QApplication

    from yulon import update as update_module
    from yulon import update_state

    asked: list[int] = []
    monkeypatch.setenv("YULON_SMOKE_TEST", "1")
    monkeypatch.setattr(update_module, "check_with_cache", lambda **kwargs: asked.append(1) or None)
    scratch = tmp_path / "config"
    scratch.mkdir()
    monkeypatch.setattr(
        update_state, "update_state_path", lambda config_dir=None: scratch / "update.json"
    )
    monkeypatch.setattr(state, "load_state", lambda path=None, repair=True: state.AppState())

    window = main.build_window()
    try:
        assert window.property("update_thread") is None, "a launch check thread was started"
        assert window.property("update_worker") is None
        assert asked == [], "the smoke run asked GitHub"
        assert not (scratch / "update.json").exists(), "the smoke run wrote update.json"
        assert window.property("update_bar") is not None, "the window was still built"
    finally:
        main._stop_background_threads(window)
        QApplication.processEvents()


def test_the_smoke_run_removes_no_previous_build(tmp_path: Path) -> None:
    """It runs with the OLD tree still installed and an `.old` possibly beside it."""
    bar = _Bar()
    install = _swapped_install(tmp_path)

    assert (
        main.announce_previous_update(bar, install=install, environ={"YULON_SMOKE_TEST": "1"})
        is False
    )

    assert (tmp_path / "app" / ".yulon-old" / "yulon").read_text(encoding="utf-8") == (
        "the previous build"
    )
    assert bar.messages == []


def test_a_half_done_swap_is_said_rather_than_tidied_away(tmp_path: Path) -> None:
    """A helper killed between two moves. The backup is the way back, and it stays."""
    from yulon.selfupdate import layout

    bar = _Bar()
    install = _swapped_install(tmp_path)
    assert install.target is not None
    (install.target / "_internal").rmdir()

    assert main.announce_previous_update(bar, install=install, environ={}, version="v2") is False

    assert bar.messages and "_internal" in bar.messages[0]
    assert layout.work_dir(install, layout.OLD_NAME).exists()


def test_a_cancel_pressed_while_the_result_was_queued_is_not_ignored(
    installing_host: Any, tmp_path: Path
) -> None:
    """**The worker's last look at the event is before it writes the helper** (S4).

    `install_done` runs on the GUI thread, queued behind whatever is in front
    of it, and a Cancel pressed in that window used to be ignored entirely: the
    app started the swap helper and closed itself under a player who had just
    said not to.
    """
    from yulon.selfupdate.apply import ReadyToRestart
    from yulon.selfupdate.swap import SwapPlan

    def apply_and_then_cancel(*_a: Any, **_k: Any) -> Any:
        # The work finished; the press lands before the result is delivered.
        installing_host.progresses[0].cancel_event.set()
        return ReadyToRestart(SwapPlan(tmp_path / "h.sh", ["/bin/sh"]), "v0.8.70-Public")

    installing_host.apply = apply_and_then_cancel

    installing_host.start_update(_install_offer())

    assert installing_host.helpers == [], "the helper was started after a cancel"
    assert installing_host.closes == [], "the app closed after a cancel"
    assert installing_host._ready is None


def test_a_cancelled_update_throws_the_staged_build_away(
    installing_host: Any, tmp_path: Path
) -> None:
    """A staged build nobody will install is 230 MB beside the player's install."""
    from yulon.selfupdate import layout
    from yulon.selfupdate.apply import ReadyToRestart
    from yulon.selfupdate.swap import SwapPlan

    install = _swappable(tmp_path)
    layout.write_marker(
        install,
        layout.NEW_NAME,
        layout.Marker(layout.NEW_NAME, "v1", "v0.8.70-Public", os.getpid(), layout.now()),
    )
    staged = layout.work_dir(install, layout.NEW_NAME)
    (staged / "yulon").write_text("the build nobody asked for", encoding="utf-8")

    def apply_and_then_cancel(*_a: Any, **_k: Any) -> Any:
        installing_host.progresses[0].cancel_event.set()
        return ReadyToRestart(SwapPlan(tmp_path / "h.sh", ["/bin/sh"]), "v0.8.70-Public")

    installing_host.apply = apply_and_then_cancel
    installing_host.start_update(_install_offer())

    assert not staged.exists(), "the staged build was left behind after a cancel"


def test_a_second_press_reuses_the_build_that_is_already_verified(
    installing_host: Any, tmp_path: Path
) -> None:
    """Downloading and unpacking 90 MB again to reach the same bytes is a minute for nothing.

    The first press stages and is refused at the gate; the second press installs
    what is already there.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    runs: list[int] = []
    answers: list[str | None] = [None, "A database import is running.", None]

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)

    def apply(*_a: Any, **_k: Any) -> Any:
        runs.append(1)
        return ReadyToRestart(plan, "v0.8.70-Public")

    installing_host.apply = apply
    installing_host.refusal = lambda: (answers.pop(0) if answers else None)

    offer = _install_offer()
    installing_host.start_update(offer)
    assert runs == [1] and installing_host.helpers == []

    installing_host.start_update(offer)

    assert runs == [1], "the second press downloaded and unpacked it all over again"
    assert len(installing_host.helpers) == 1 and installing_host.closes == [1]


def test_a_staged_build_for_a_DIFFERENT_version_is_not_reused(
    installing_host: Any, tmp_path: Path
) -> None:
    """The offer moved on while the update was waiting; the old staging is not it."""
    from yulon.selfupdate.apply import ReadyToRestart
    from yulon.selfupdate.swap import SwapPlan

    runs: list[int] = []
    install = _a_staged_install(tmp_path, version="v0.8.99-Public")
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)

    def apply(*_a: Any, **_k: Any) -> Any:
        runs.append(1)
        return ReadyToRestart(plan, "v0.8.99-Public")

    installing_host.apply = apply
    installing_host._ready = ReadyToRestart(
        SwapPlan(tmp_path / "old.sh", ["/bin/sh"], ("yulon",), "#!/bin/sh\n", "", ("/bin/sh", "x")),
        "v0.8.70-Public",
    )
    installing_host.start_update(
        dataclasses.replace(
            _install_offer(),
            latest="v0.8.99-Public",
            assets=(
                update.ReleaseAsset(
                    "Yulon-v0.8.99-Public-x86_64.tar.gz", "https://example.invalid/a", 1234
                ),
                update.ReleaseAsset("SHA256SUMS", "https://example.invalid/s", 10),
            ),
        )
    )

    assert runs == [1], "a staging for another version was installed"
    assert installing_host.helpers[0].fixed == plan.fixed, "another version's plan was used"


# ------------------------------------- never close for a helper that did not start


def _a_staged_install(tmp_path: Path, *, version: str = "v0.8.70-Public") -> Any:
    """A marked, complete staging exactly as `apply_update` leaves one."""
    from yulon.selfupdate import layout

    install = _swappable(tmp_path)
    assert install.target is not None
    for name in (layout.NEW_NAME, layout.OLD_NAME):
        layout.work_dir(install, name).mkdir(parents=True, exist_ok=True)
        layout.write_marker(
            install,
            name,
            layout.Marker(name, "v1", version, os.getpid(), layout.now(), entries=("yulon",)),
        )
    (layout.work_dir(install, layout.NEW_NAME) / "yulon").write_text("new", encoding="utf-8")
    return install


def _a_plan(tmp_path: Path, install: Any) -> Any:
    """A plan shaped as `plan_swap` leaves one: no script yet, and the parts to arm it.

    Since round 4 a plan is a DESCRIPTION of the swap; `arm()` turns it into one
    attempt with its own nonce and its own file, immediately before the helper
    is started. So there is nothing on disk here, deliberately.
    """
    from yulon.selfupdate.swap import SwapPlan

    target = install.target
    fixed = (
        "/bin/sh",
        str(tmp_path),
        "3",
        str(os.getpid()),
        str(target),
        str(target / "yulon"),
        "1",
        "yulon",
        "yulon",
    )
    return SwapPlan(Path(), [], ("yulon",), "#!/bin/sh\nexit 0\n", "", fixed)


def test_the_window_is_not_closed_when_the_helper_never_reports_in(
    installing_host: Any, tmp_path: Path
) -> None:
    """**The Windows 11 gate, 2026-09-21.**

    The helper was spawned and never ran — `DETACHED_PROCESS` leaves PowerShell
    5.1 without a console — and the app closed anyway: a shut launcher, an
    un-swapped folder, and not a word to the player. A `Popen` that returned is
    not a helper that is running, and this is what says so.
    """
    from yulon.selfupdate import layout
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    # A helper that starts and stamps nothing, which is exactly what happened.
    installing_host.await_helper = lambda _stamp, _nonce: False

    installing_host.start_update(_install_offer())

    assert len(installing_host.helpers) == 1, "the helper was never started"
    assert installing_host.closes == [], "the app closed for a helper that had not started"
    bar = installing_host.parent().property("update_bar")
    assert "could not start the installer" in bar.text()
    assert "nothing was changed" in bar.text()
    assert installing_host._ready is not None, "the staged build was thrown away"
    # **And it is not left running.** Both halves: the file a helper reads on
    # every tick, and the process itself.
    nonce = installing_host.helpers[0].nonce
    stood_down = layout.stand_down_path(install)
    assert stood_down.exists() and nonce in stood_down.read_text(encoding="utf-8")
    assert len(installing_host.ended) == 1, "the helper was left running"


def test_the_window_closes_once_the_helper_has_reported_in(
    installing_host: Any, tmp_path: Path
) -> None:
    """And the other half: a helper that stamps is one the app may close for."""
    from yulon.selfupdate import layout
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    order: list[str] = []

    def start(p: Any) -> Any:
        order.append("helper")
        # What the real helper does first, after validating: its own nonce.
        layout.helper_stamp(install).write_text(p.nonce, encoding="utf-8")
        return None

    installing_host.start_helper = start
    installing_host.close_window = lambda: order.append("close")
    seen: list[tuple[Path, str]] = []

    def awaited(stamp: Path, nonce: str) -> bool:
        seen.append((stamp, nonce))
        return main.wait_for_helper(stamp, seconds=1.0, holds=nonce)

    installing_host.await_helper = awaited

    installing_host.start_update(_install_offer())

    assert order == ["helper", "close"], "the app closed before the helper reported in"
    assert [s for s, _ in seen] == [layout.helper_stamp(install)]
    assert seen[0][1] == installing_host.helpers[0].nonce if installing_host.helpers else True
    assert installing_host.ended == [], "a helper that was working was terminated"


def test_the_real_wait_gives_up_and_says_so(tmp_path: Path) -> None:
    """`wait_for_helper` itself, bounded and measured.

    Its bound is deliberately short here: what is under test is that it RETURNS
    rather than that it waits a particular length of time.
    """
    import time as _time

    stamp = tmp_path / "helper-started"
    started = _time.monotonic()
    assert main.wait_for_helper(stamp, seconds=0.2, holds="abc123") is False
    waited = _time.monotonic() - started
    assert 0.1 <= waited < 5.0, f"the wait took {waited:.2f}s"

    # **A stamp from another attempt answers nothing** (round 4, M2): nothing
    # removed the file, so a helper that had already given up left one behind
    # and the next press closed the app in 0.04 s against a spawn that had
    # started nothing at all.
    stamp.write_text("999888777\n", encoding="utf-8")
    assert main.wait_for_helper(stamp, seconds=0.2, holds="abc123") is False

    stamp.write_text("abc123\n", encoding="utf-8")
    assert main.wait_for_helper(stamp, seconds=0.2, holds="abc123") is True


def test_a_staged_build_that_vanished_does_not_close_the_app(
    installing_host: Any, tmp_path: Path
) -> None:
    """A second copy of Yu'lon, a `/tmp` sweep, or the player (round 3, F1).

    The app used to close for a helper that then exited 64 against work
    directories that were no longer there, and nothing reopened it.
    """
    from yulon.selfupdate import layout
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    installing_host.start_helper = installing_host.helpers.append
    # Between staging and pressing, somebody cleared the work dirs.
    for name in layout.WORK_NAMES:
        layout.discard_ours(install, name)

    installing_host.start_update(_install_offer())

    assert installing_host.helpers == [], "a helper was started against nothing"
    assert installing_host.closes == []
    bar = installing_host.parent().property("update_bar")
    assert "no longer there" in bar.text() and "press Update now again" in bar.text()


def test_a_helper_script_swept_from_the_temp_directory_is_written_again(
    installing_host: Any, tmp_path: Path
) -> None:
    """The script lives in `tempfile.gettempdir()`, which a desktop sweeps.

    Which is why it is written at the moment of use, by `arm()`, and not when
    the build was staged minutes earlier.
    """
    from yulon.selfupdate import layout
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    swept = tmp_path / "swept"
    swept.mkdir()
    plan = dataclasses.replace(plan, fixed=("/bin/sh", str(swept), *plan.fixed[2:]))
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")

    def start(p: Any) -> Any:
        installing_host.helpers.append(p)
        layout.helper_stamp(install).write_text(p.nonce, encoding="utf-8")
        return None

    installing_host.start_helper = start
    installing_host.await_helper = lambda stamp, nonce: layout.stamp_holds(install, nonce)

    installing_host.start_update(_install_offer())

    written = installing_host.helpers[0].script
    assert written.parent == swept and written.exists(), "the helper script was not written"
    assert written.read_text(encoding="utf-8") == plan.body
    assert installing_host.closes == [1]


def test_a_second_press_while_the_app_is_waiting_for_the_helper_does_nothing(
    installing_host: Any, tmp_path: Path
) -> None:
    """**Two helpers on one install** (round 4, M3), through the real host object.

    The wait calls `QApplication.processEvents()`, which delivers whatever is
    queued — including the click on Update now that a player gives a window
    that has not closed yet. That press re-entered and armed a SECOND helper on
    the same install; the two of them raced, and the loser moved the winner's
    new executable into a backup that then held nothing to go back to.

    So the press is made from inside the wait, which is where it really
    arrives, and what is counted is how many builds were applied, how many
    helpers were started, and how many times the window was closed.
    """
    from yulon.selfupdate import layout
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    applies: list[int] = []
    offer = _install_offer()

    def apply(*_a: Any, **_k: Any) -> Any:
        applies.append(1)
        return ReadyToRestart(plan, "v0.8.70-Public")

    def start(p: Any) -> Any:
        installing_host.helpers.append(p)
        layout.helper_stamp(install).write_text(p.nonce, encoding="utf-8")
        return None

    presses: list[int] = []

    def awaited(stamp: Path, nonce: str) -> bool:
        # The queued click, delivered exactly where `processEvents()` delivers it.
        if not presses:
            presses.append(1)
            installing_host.start_update(offer)
            installing_host.restart_into(ReadyToRestart(plan, "v0.8.70-Public"))
        return layout.stamp_holds(install, nonce)

    installing_host.apply = apply
    installing_host.start_helper = start
    installing_host.await_helper = awaited

    installing_host.start_update(offer)

    assert presses == [1], "the re-entrant press never happened"
    assert applies == [1], "the update ran twice"
    assert len(installing_host.helpers) == 1, "two helpers were started on one install"
    assert installing_host.closes == [1], "the window was closed twice"


def test_the_guard_is_released_even_when_the_attempt_raises(
    installing_host: Any, tmp_path: Path
) -> None:
    """One attempt at a time must not become no attempts ever.

    `_restarting` is held across the whole handshake, so it is released in a
    `finally`: a seam that raises would otherwise leave the app refusing every
    later press with nothing on screen to say why.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    ready = ReadyToRestart(plan, "v0.8.70-Public")

    def boom(_stamp: Path, _nonce: str) -> bool:
        raise RuntimeError("the wait blew up")

    installing_host.await_helper = boom
    with pytest.raises(RuntimeError):
        installing_host.restart_into(ready)

    assert installing_host._restarting is False, "the app can never try again"


def test_a_helper_that_will_not_start_is_reported_and_not_raised(
    installing_host: Any, tmp_path: Path
) -> None:
    """**Nothing may escape this slot** (round 4).

    `install_done` is a queued `@Slot` on the GUI thread: an exception here is
    a traceback nobody sees, an app that has staged a whole build, and no word
    to the player. AppLocker, a missing shell and a full disk all raise from
    the spawn.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")

    def refuse(_plan: Any) -> Any:
        raise PermissionError("[WinError 5] Access is denied")

    installing_host.start_helper = refuse

    installing_host.start_update(_install_offer())

    assert installing_host.closes == [], "the app closed for a helper that never started"
    bar = installing_host.parent().property("update_bar")
    assert "could not start the installer" in bar.text()
    assert "Access is denied" in bar.text(), "the reason was not passed on"
    assert installing_host._ready is not None, "the staged build was thrown away"
    assert installing_host._restarting is False


def test_a_failed_attempt_releases_the_lock_before_it_clears_the_stamp(
    installing_host: Any, tmp_path: Path
) -> None:
    """**The lock outlives the process that took it** (round 5, M1).

    `/bin/sh` is dash on most Linuxes and dash runs no EXIT trap on SIGTERM;
    Windows `TerminateProcess` runs nothing at all. So a helper the app stops
    leaves `helper.lock` behind, and every later press then got exit 73 and no
    stamp for the rest of the session. The app removes it — but only after the
    process is confirmed gone, and only its own.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    installing_host.await_helper = lambda _stamp, _nonce: False

    installing_host.start_update(_install_offer())

    armed = installing_host.helpers[0]
    assert len(installing_host.ended) == 1, "the helper was not stopped"
    assert [p.nonce for p in installing_host.released] == [armed.nonce], "the lock was left"
    assert installing_host._helper is None, "the app thinks a helper is still running"
    bar = installing_host.parent().property("update_bar")
    assert "Press Update now again" in bar.text()


def test_a_helper_that_will_not_die_stops_the_session_trying_again(
    installing_host: Any, tmp_path: Path
) -> None:
    """The other half of M1: if it would not stop, a second one must not go in beside it.

    Saying "press Update now again" here is what would send a second helper
    into an install a live one is holding — the two-helper race, arrived at
    from the other direction.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    offer = _install_offer()
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    installing_host.await_helper = lambda _stamp, _nonce: False
    installing_host.end_helper = lambda handle: False  # it ignored everything

    installing_host.start_update(offer)

    assert installing_host.released == [], "a lock a live helper holds was released"
    assert installing_host._helper is not None
    bar = installing_host.parent().property("update_bar")
    assert "could not stop it either" in bar.text()
    assert "Close Yu'lon and open it again" in bar.text()

    # And the second press starts nothing at all.
    started = len(installing_host.helpers)
    installing_host.start_update(offer)
    assert len(installing_host.helpers) == started, "a second helper went in beside a live one"
    assert installing_host.closes == []


def test_a_script_nothing_ever_ran_is_removed(installing_host: Any, tmp_path: Path) -> None:
    """One `.ps1` per update stayed in `%TEMP%` for ever (round 5, from the Windows gate).

    A helper that starts deletes its own script; these are the paths where no
    helper ever ran. Here the spawn itself raises, which is AppLocker, a
    missing shell or a full disk.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")

    def refuse(_plan: Any) -> Any:
        raise PermissionError("[WinError 5] Access is denied")

    installing_host.start_helper = refuse

    installing_host.start_update(_install_offer())

    assert len(installing_host.discarded) == 1, "the script was left in the temp directory"
    assert installing_host.discarded[0].nonce, "the armed plan was not the one discarded"


def test_a_cancelled_update_takes_its_script_with_it(installing_host: Any, tmp_path: Path) -> None:
    """The same for a staged build nobody is going to install."""
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host._ready = ReadyToRestart(plan, "v0.8.70-Public")

    installing_host.discard_staged()

    assert installing_host.discarded == [plan]
    assert installing_host._ready is None


def test_only_one_helper_script_is_written_for_one_attempt(
    installing_host: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """**Two were written and one was orphaned** (round 5, from the Windows gate).

    `plan_swap` armed the plan it returned, minutes before the press, and
    `restart_into` armed it again; only the second was ever started, and only a
    started helper deletes its own script. Here the REAL `arm` runs, into a
    directory of its own, and what is counted is the files on disk.
    """
    import logging

    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    plan = _a_plan(tmp_path, install)
    plan = dataclasses.replace(plan, fixed=("/bin/sh", str(scripts), *plan.fixed[2:]))
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")

    with caplog.at_level(logging.INFO, logger="yulon.selfupdate.swap"):
        installing_host.start_update(_install_offer())

    written = sorted(scripts.glob("yulon-update-*"))
    assert len(written) == 1, f"{len(written)} scripts were written: {written}"
    assert written[0] == installing_host.helpers[0].script
    said = [r for r in caplog.records if "helper written" in r.getMessage()]
    assert len(said) == 1, f"the log says a script was written {len(said)} times"


def test_the_wait_refuses_a_nonce_that_is_not_one(tmp_path: Path) -> None:
    """**`"" in anything` is True** (round 5, N5).

    An empty nonce made the wait answer yes to any stamp at all, including one
    an earlier attempt had left — which is precisely what the nonce was added
    to stop. It is a programming error, and it closes the window, so it raises.
    """
    stamp = tmp_path / "helper-started"
    stamp.write_text("somebody else's attempt", encoding="utf-8")
    with pytest.raises(ValueError, match="nonce"):
        main.wait_for_helper(stamp, seconds=0.1, holds="")


def test_a_lock_nothing_can_free_is_said_once_rather_than_pressed_for_ever(
    installing_host: Any, tmp_path: Path
) -> None:
    """**Never loop the player** (round 6).

    A helper that exits 73 found the lock already held — by a helper that was
    SIGKILLed, or one that died between `mkdir` and its owner write. The app
    only sees "no stamp", and its answer was "press Update now again", which
    asks the player to watch the same thing happen for the rest of the
    session. The lock is looked at instead, and a holder that will not let go
    is named with the way out.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    installing_host.await_helper = lambda _stamp, _nonce: False
    installing_host.make_way = lambda _install, **_kw: (
        "An installer Yu'lon started earlier is still working in this folder."
    )

    installing_host.start_update(_install_offer())

    bar = installing_host.parent().property("update_bar")
    assert "still working in this folder" in bar.text()
    assert "Press Update now again" not in bar.text(), "the player was sent round the loop"
    assert installing_host.closes == []


def test_a_lock_that_was_only_rubbish_leaves_the_ordinary_message(
    installing_host: Any, tmp_path: Path
) -> None:
    """And when the way is clear, the press the message asks for is worth making."""
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    installing_host.await_helper = lambda _stamp, _nonce: False
    asked: list[Any] = []

    def watched(inst: Any, **kw: Any) -> None:
        asked.append((inst, kw.get("tick")))
        return None

    installing_host.make_way = watched

    installing_host.start_update(_install_offer())

    assert len(asked) == 1, "the lock was never looked at"
    assert asked[0][1] is main.pump, "the GUI thread would stop repainting while it waits"
    bar = installing_host.parent().property("update_bar")
    assert "Press Update now again" in bar.text()


def test_the_script_of_a_helper_that_is_still_running_is_not_deleted(
    installing_host: Any, tmp_path: Path
) -> None:
    """**A live process's script is not rubbish** (round 6, N3a).

    After a helper that would not stop, `_ready` holds that attempt — and a
    cancel, or any other discard, would otherwise unlink the file that process
    is running from. The kernel keeps an open file on Linux; Windows does not
    have to, and either way this breaks the invariant the rest of the round is
    built on.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    installing_host.apply = lambda *a, **k: ReadyToRestart(plan, "v0.8.70-Public")
    installing_host.await_helper = lambda _stamp, _nonce: False
    installing_host.end_helper = lambda handle: False  # it would not stop

    installing_host.start_update(_install_offer())
    assert installing_host._helper is not None, "the precondition: a helper is still running"

    installing_host.discard_staged()

    assert installing_host.discarded == [], "the running helper's script was deleted"
    assert installing_host._ready is None, "the staged build was kept"


def test_a_click_delivered_while_the_lock_is_waited_on_starts_nothing(
    installing_host: Any, tmp_path: Path
) -> None:
    """The tick pumps events, and an event can be a press (round 7, S1).

    `pump()` is `processEvents()`, so anything queued runs — including the
    click on Update now that a player gives a window which has not closed yet.
    `_restarting` is held across the whole handshake and that is what has to
    cover this; here the press is made from inside the tick, which is exactly
    where `processEvents()` would deliver it.
    """
    from yulon.selfupdate.apply import ReadyToRestart

    install = _a_staged_install(tmp_path)
    installing_host.current_install = lambda: install
    plan = _a_plan(tmp_path, install)
    offer = _install_offer()
    applies: list[int] = []

    def apply(*_a: Any, **_k: Any) -> Any:
        applies.append(1)
        return ReadyToRestart(plan, "v0.8.70-Public")

    def waited(_install: Any, **kw: Any) -> None:
        # One tick, and the press arrives in it.
        tick = kw.get("tick")
        assert tick is not None
        installing_host.start_update(offer)
        return None

    installing_host.apply = apply
    installing_host.await_helper = lambda _stamp, _nonce: False
    installing_host.make_way = waited

    installing_host.start_update(offer)

    assert applies == [1], "the update ran twice"
    assert len(installing_host.helpers) == 1, "a second helper was started from a tick"
    assert installing_host.closes == []


def test_the_windows_flags_are_the_ones_the_gate_settled() -> None:
    """**`DETACHED_PROCESS` is not among them**, and that is the whole finding.

    Measured on Windows 11, 2026-09-21: with it, the helper was spawned and
    never ran at any of 40 samples over two minutes. Microsoft documents it as
    mutually exclusive with `CREATE_NO_WINDOW`, and PowerShell 5.1 with no
    console at all exits immediately.
    """
    from types import SimpleNamespace

    from yulon.selfupdate.swap import windows_creation_flags

    fake = SimpleNamespace(
        CREATE_NO_WINDOW=0x08000000,
        CREATE_NEW_PROCESS_GROUP=0x00000200,
        CREATE_BREAKAWAY_FROM_JOB=0x01000000,
        DETACHED_PROCESS=0x00000008,
    )
    with_breakaway = windows_creation_flags(fake)
    assert with_breakaway == 0x08000000 | 0x00000200 | 0x01000000
    assert not with_breakaway & fake.DETACHED_PROCESS, "DETACHED_PROCESS is back"

    without = windows_creation_flags(fake, breakaway=False)
    assert without == 0x08000000 | 0x00000200
    assert not without & fake.CREATE_BREAKAWAY_FROM_JOB


# ------------------------------------------- closing while a background job is stuck (T113)

# A child process for `_ENTRY_POINT`'s reason, and a second one: the thing
# under test is what happens AFTER `main()` - interpreter teardown, where Qt
# aborts on a QThread destroyed while running - and no in-process test can
# reach past its own interpreter's end. The window is the REAL one; only the
# GitHub update check is stubbed (it would ask the network and write
# `update.json`), and the docker-group re-exec, which would replace the child.
_CLOSE_WITH_A_JOB = """\
import os, sys, threading, time

sys.argv = ["yulon"]
from yulon import log, update

kind = os.environ["YULON_TEST_JOB"]
never = threading.Event()
entered = threading.Event()


def an_update_check_that_never_returns(*_a, **_k) -> None:
    never.wait()


update.check_with_cache = (
    an_update_check_that_never_returns if kind == "stuck-update" else (lambda *a, **k: None)
)

from PySide6.QtCore import QObject, QTimer, Slot

import main
from yulon.ui.widgets.job import threaded_job_runner

main._regain_docker_group = lambda: None


def a_job_that_never_returns() -> None:
    never.wait()


def a_job_that_returns() -> None:
    return None


def a_log_source_that_never_ends():
    entered.set()
    never.wait()
    yield "never reached"


class _Ignore(QObject):
    @Slot(object)
    def outcome(self, _outcome: object) -> None:
        pass


real_build_window = main.build_window


def build_window():
    window = real_build_window()
    ignore = _Ignore(window)
    runner = threaded_job_runner(window)
    work = a_job_that_never_returns if kind == "stuck" else a_job_that_returns

    def start_then_close() -> None:
        if kind == "stuck-panel":
            # The first panel is the catalog's install log, and `LogPanel.run`
            # is the real entry: the same call an install makes.
            window.yulon_log_panels[0].run(a_log_source_that_never_ends, title="following")
            # Closed only once the source is ENTERED (T156). The exit's
            # `panel.stop()` reaching a worker the OS has not scheduled yet is
            # the "stopped before it started" path: the worker returns without
            # touching the source, nothing is stuck, and `main()` returns 0.
            # Six busy loops on three cores made that 19 runs in 40, and a
            # one-second delay before the worker's first line made it every run.
            # Blocking here is safe: the worker is started on its own thread,
            # not through this one's queue.
            print(f"T113 source entered {entered.wait(60)}", flush=True)
        elif kind != "stuck-update":
            runner(work, ignore.outcome, ignore.outcome)
        print(f"T113 log {log.file_path()}", flush=True)
        print(f"T113 closing at {time.time()}", flush=True)
        window.close()

    QTimer.singleShot(0, start_then_close)
    return window


main.build_window = build_window
code = main.main()
print(f"T113 main returned {code}", flush=True)
raise SystemExit(code)
"""

EXIT_MARGIN_SECONDS = 10.0
"""What the stuck close may take beyond `main.EXIT_JOIN_MS`, the join it has to sit out.

The rest of the exit is ending abandoned `stream()` children (none here),
flushing the log and `os._exit`. Measured on the laptop 2026-09-25 before the
fix: 8.3 s from `window.close()` to the abort, i.e. the join plus 0.3 s. Ten
seconds is headroom for a loaded box, not an expectation.
"""


def _close_the_real_window_with(job: str, tmp_path: Path) -> tuple[Any, float, float, str]:
    """Run `_CLOSE_WITH_A_JOB`; answer (process, closed at, ended at, the log file's text)."""
    home = tmp_path / "home"
    home.mkdir()
    scratch_temp = tmp_path / "temp"
    scratch_temp.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "YULON_TEST_JOB": job,
            "HOME": str(home),  # macOS: config_dir() is under HOME and nothing else
            "APPDATA": str(home),
            "XDG_DATA_HOME": str(home),
            "QT_QPA_PLATFORM": "offscreen",
            "TMPDIR": str(scratch_temp),
            "TEMP": str(scratch_temp),
            "TMP": str(scratch_temp),
        }
    )
    for name in ("YULON_SMOKE_TEST", "YULON_PROVISION"):
        env.pop(name, None)
    pylauncher = Path(main.__file__).parent
    env["PYTHONPATH"] = str(pylauncher)

    done = subprocess.run(
        [sys.executable, "-c", _CLOSE_WITH_A_JOB],
        cwd=pylauncher,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    ended = time.time()
    said = done.stdout.splitlines()
    closing = [line for line in said if line.startswith("T113 closing at ")]
    logged = [line for line in said if line.startswith("T113 log ")]
    assert closing and logged, f"the window never closed:\n{done.stdout}\n{done.stderr}"
    closed_at = float(closing[0].removeprefix("T113 closing at "))
    log_file = Path(logged[0].removeprefix("T113 log "))
    return done, closed_at, ended, log_file.read_text(encoding="utf-8")


@pytest.mark.slow
def test_closing_while_a_job_is_stuck_exits_cleanly_and_names_the_job(tmp_path: Path) -> None:
    """T113: a job blocked forever must not turn a close into a crash report.

    Before the fix, measured on the laptop 2026-09-25: `main()` returned 0,
    and then interpreter teardown destroyed the QThread `InFlight` still held
    and Qt aborted - "QThread: Destroyed while thread '' is still running",
    exit 134, a core dump on Linux and a crash report on Windows and macOS.

    The log is read from the FILE, not stderr, because the forced exit skips
    `logging.shutdown()`'s atexit hook and a line still in a buffer is a line
    support never sees. "main returned" must be absent: the process left
    through `main._hard_exit`, not by returning into an interpreter teardown
    that would abort on the running thread.
    """
    done, closed_at, ended, log_text = _close_the_real_window_with("stuck", tmp_path)

    report = f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
    assert "QThread: Destroyed" not in done.stderr, report
    assert done.returncode == 0, report
    assert "T113 main returned" not in done.stdout, report
    assert ended - closed_at <= main.EXIT_JOIN_MS / 1000 + EXIT_MARGIN_SECONDS, report
    assert "a_job_that_never_returns" in log_text, f"the log does not name the job:\n{log_text}"


@pytest.mark.parametrize(
    ("job", "named"),
    [
        ("stuck-panel", 'log panel "following"'),
        ("stuck-update", "the launch update check"),
    ],
)
@pytest.mark.slow
def test_a_stuck_log_panel_or_update_check_reaches_the_forced_exit_by_name(
    job: str, named: str, tmp_path: Path
) -> None:
    """The two threads `_stop_background_threads()` joins itself, stuck (T113, Codex pass).

    A log panel following a source that never yields, and a launch update
    check that never returns. Each must end at the forced exit with the code
    `main()` would have returned (0), and the log must name it by what it IS -
    the panel's job, the update check - not by a worker class every panel
    shares. Measured on 3a6c9933, before the joins were collected: both
    already exited 0, because `in_flight()` holds these threads too and its
    `wait_all` caught them, but the log said `_StreamWorker` and
    `_UpdateWorker`.

    The panel is only stuck once its worker has entered the source: closed
    before the OS schedules that thread, the exit's stop is honoured before the
    source is touched and `main()` returns 0 - correct, and not this test's
    case. The child waits for the source before it closes, and says so (T156).
    The update check needs no such wait: its thread counts as running from
    `start()`, and its worker has no stop to honour before the check.
    """
    done, closed_at, ended, log_text = _close_the_real_window_with(job, tmp_path)

    report = f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
    if job == "stuck-panel":
        assert "T113 source entered True" in done.stdout, report
    assert "QThread: Destroyed" not in done.stderr, report
    assert done.returncode == 0, report
    assert "T113 main returned" not in done.stdout, report
    closing = [line for line in log_text.splitlines() if "still running:" in line]
    assert closing and named in closing[0], f"the log does not name {named}:\n{log_text}"
    assert closing[0].count(named) == 1, f"named twice:\n{closing[0]}"
    bound = (main.PANEL_JOIN_MS + main.UPDATE_JOIN_MS + main.EXIT_JOIN_MS) / 1000
    assert ended - closed_at <= bound + EXIT_MARGIN_SECONDS, report


def test_closing_with_every_job_finished_returns_from_main_as_before(tmp_path: Path) -> None:
    """The normal close is untouched: `main()` returns, and nothing is called stuck."""
    done, closed_at, ended, log_text = _close_the_real_window_with("finishes", tmp_path)

    report = f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
    assert done.returncode == 0, report
    assert "T113 main returned 0" in done.stdout, report
    assert "QThread: Destroyed" not in done.stderr, report
    assert "still running" not in log_text, log_text
    assert ended - closed_at < main.EXIT_JOIN_MS / 1000, report


def test_the_forced_exit_ends_streams_and_flushes_the_log_before_it_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order `os._exit` makes the app responsible for, since no atexit hook runs.

    `runner._close_abandoned_streams` first, so a `docker logs -f` child is not
    left behind with PPID 1 (and so its own log lines are written); then the
    log handlers are flushed and closed; then the exit, with the code `main()`
    was going to return. PySide's `__moduleShutdown` and `pygame.quit` are the
    two atexit hooks deliberately NOT run - see `_leave_with_jobs_still_running`.
    """
    import logging

    from yulon import runner

    calls: list[str] = []
    monkeypatch.setattr(runner, "_close_abandoned_streams", lambda: calls.append("streams"))
    monkeypatch.setattr(logging, "shutdown", lambda: calls.append("log"))
    monkeypatch.setattr(main, "_hard_exit", lambda code: calls.append(f"exit {code}"))

    main._leave_with_jobs_still_running(3, ["the launch update check"])

    assert calls == ["streams", "log", "exit 3"]


_REAL_HARD_EXIT = main._hard_exit
"""`main._hard_exit` as this module found it at import, before any fixture ran.

Collection imports test modules before the first test's fixtures, so this is
the real seam - the function that calls `os._exit` - and never conftest's stub.
"""


def test_no_test_can_reach_the_real_os_exit_in_process() -> None:
    """`conftest.py` swaps the seam out for every test; a forced exit would end pytest.

    Compared with the function captured at import, not with `os._exit`: the
    seam is a function that CALLS `os._exit`, so `is not os._exit` held with
    or without the guard. The identity check comes first so that, with the
    fixture removed, this fails on the assert instead of calling the real one.
    """
    assert main._hard_exit is not _REAL_HARD_EXIT, "conftest's _no_forced_exit is not applied"
    with pytest.raises(AssertionError, match="was reached in-process"):
        main._hard_exit(0)


def test_a_join_that_fails_counts_even_when_nothing_else_holds_the_thread(qapp: object) -> None:
    """Every join `_stop_background_threads()` makes counts, not only `wait_all`'s (T113).

    Today each panel's thread is also held by `in_flight()`, which is why the
    child-process tests above would pass on `wait_all` alone. This is the case
    that coincidence hides: a panel whose join failed and whose thread nobody
    else holds must still be named, or `main()` returns into the Qt abort.
    """
    from types import SimpleNamespace

    class _StuckPanel:
        job_label = 'log panel "a thread nobody else holds"'
        running = True

        def stop(self) -> None:
            pass

        def wait(self, _timeout_ms: int) -> bool:
            return False

    window = SimpleNamespace(yulon_log_panels=[_StuckPanel()], property=lambda _name: None)

    assert main._stop_background_threads(window) == [_StuckPanel.job_label]


def test_a_panel_that_finished_during_the_last_join_is_not_called_stuck(qapp: object) -> None:
    """A panel that missed its own join but is done by the end is no reason to force the exit."""
    from types import SimpleNamespace

    class _LatePanel:
        job_label = 'log panel "late but done"'
        running = False

        def stop(self) -> None:
            pass

        def wait(self, _timeout_ms: int) -> bool:
            return False

    window = SimpleNamespace(yulon_log_panels=[_LatePanel()], property=lambda _name: None)

    assert main._stop_background_threads(window) == []


# ------------------------------------ T181a: the ready-to-play client folder seam


def test_the_play_client_seam_writes_the_record_and_keeps_the_rest_of_it(
    window: Any, tmp_path: Any
) -> None:
    """`main._remember_play_client_live()`: the same live-`AppState` write as T36's seam."""
    server_dir = tmp_path / "play-client-live"
    client = tmp_path / "WoW"
    catalog = _catalog_view(window)
    catalog.adopted.emit("wow-wotlk", server_dir, client, "Ubuntu-24.04")
    view = _tab_for(window, server_dir)
    assert view.services.set_play_client_dir is not None
    play = tmp_path / "WoW (Yu'lon)"

    view.services.set_play_client_dir(play)

    after = window.saved_states[-1].find("wow-wotlk", server_dir)
    assert after is not None
    assert after.play_client_dir == play
    assert after.client_dir == client, "the original folder was dropped"
    assert after.wsl_distro == "Ubuntu-24.04", "the distro was dropped"

    view.services.set_play_client_dir(None)

    cleared = window.saved_states[-1].find("wow-wotlk", server_dir)
    assert cleared is not None and cleared.play_client_dir is None
    assert cleared.client_dir == client


def test_a_failing_play_client_save_restores_the_old_record(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = tmp_path / "play-client-restore"
    seen_states: list[Any] = []

    def _refuse(app_state: Any, path: Any = None) -> None:
        seen_states.append(app_state)
        raise PermissionError(13, "Access is denied", "state.json")

    monkeypatch.setattr(state, "save_state", _refuse)
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)

    with pytest.raises(OSError):
        view.services.set_play_client_dir(tmp_path / "WoW (Yu'lon)")

    restored = seen_states[-1].find("wow-wotlk", server_dir)
    assert restored is not None
    assert restored.play_client_dir is None, "the failed write was not undone"


def test_a_rebuilt_tab_keeps_writing_modules_into_the_ready_to_play_client(
    window: Any, tmp_path: Any
) -> None:
    """A client-folder press rebuilds the tab, which reads the record's ready-to-play client."""
    server_dir = tmp_path / "tw-play-client"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-tortoise", server_dir, None)
    view = _tab_for(window, server_dir)
    client = tmp_path / "TurtleWoW"
    play = tmp_path / "TurtleWoW (Yu'lon)"
    for folder in (client, play):
        (folder / "Interface").mkdir(parents=True)
    view.services.set_play_client_dir(play)
    view.services.set_client_dir(client)
    view.client_dir_changed.emit("wow-tortoise", server_dir, client)

    rebuilt = _tab_for(window, server_dir)
    assert rebuilt is not view
    assert rebuilt.services.client_dir == client
    assert rebuilt.services.play_client_dir == play
    assert rebuilt.services.applier is not None
    assert rebuilt.services.applier.client_dir == play, "modules went back to the original"


def test_use_existing_on_a_known_server_keeps_its_ready_to_play_client(
    window: Any, tmp_path: Any
) -> None:
    """`installed` replaces the record; its ready-to-play client is carried over like the distro."""
    server_dir = tmp_path / "play-client-reinstalled"
    client = tmp_path / "WoW"
    play = tmp_path / "WoW (Yu'lon)"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", server_dir, client)
    _tab_for(window, server_dir).services.set_play_client_dir(play)

    catalog.installed.emit("wow-wotlk", server_dir, client)

    kept = window.saved_states[-1].find("wow-wotlk", server_dir)
    assert kept is not None and kept.play_client_dir == play


def test_a_made_or_deleted_play_client_rebuilds_the_tab_with_the_new_wiring(
    window: Any, tmp_path: Any
) -> None:
    """T181a carried finding 1: `play_client_dir_changed` rebuilds the tab, as T36's signal does."""
    server_dir = tmp_path / "tw-play-made"
    catalog = _catalog_view(window)
    client = tmp_path / "TurtleWoW"
    play = tmp_path / "TurtleWoW (Yu'lon)"
    for folder in (client, play):
        (folder / "Interface").mkdir(parents=True)
    catalog.installed.emit("wow-tortoise", server_dir, client)
    view = _tab_for(window, server_dir)
    assert view.services.set_play_client_dir is not None
    view.services.set_play_client_dir(play)
    view.play_client_dir_changed.emit("wow-tortoise", server_dir, play)

    made = _tab_for(window, server_dir)
    assert made is not view, "the old tab was kept"
    assert made.services.play_client_dir == play
    assert made.services.client_dir == client
    assert made.services.applier is not None and made.services.applier.client_dir == play

    made.services.set_play_client_dir(None)
    made.play_client_dir_changed.emit("wow-tortoise", server_dir, None)

    gone = _tab_for(window, server_dir)
    assert gone is not made
    assert gone.services.play_client_dir is None
    assert gone.services.applier is not None and gone.services.applier.client_dir == client


# -- T188 C6: the header's realm badge is the current server tab's ------------


def test_the_header_badge_follows_the_server_tab_on_screen(window: Any, tmp_path: Any) -> None:
    """Audit C6: the header said REALM OFFLINE for a realm the tab said was online."""
    header = window.property("header")
    tabs = window.property("tabs")
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", tmp_path / "c6-first", None)
    first = _tab_for(window, tmp_path / "c6-first")
    catalog.installed.emit("wow-wotlk", tmp_path / "c6-second", None)
    second = _tab_for(window, tmp_path / "c6-second")
    first.realm_badge.set_status("running")
    second.realm_badge.set_status("stopped")

    tabs.setCurrentWidget(first)
    assert header._badge.status == "running"
    assert header._badge.isHidden() is False

    tabs.setCurrentWidget(second)
    assert header._badge.status == "stopped"
    first.realm_badge.set_status("starting")
    assert header._badge.status == "stopped", "it followed a tab that is not on screen"
    second.realm_badge.set_status("stopping")
    assert header._badge.status == "stopping"


def test_a_window_opened_with_no_servers_hides_the_header_badge(window: Any) -> None:
    """T188 fix round 1 (M6): the Catalog was current before the header listened."""
    assert window.header_badge_hidden_at_start is True


def test_a_window_opened_with_a_server_installed_follows_its_badge(
    qapp: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T210: the T179 live check opened Yu'lon onto a server already installed.

    The header read REALM OFFLINE while the Server tab said REALM ONLINE. Every
    other header test adds its tab to the shared window AFTER start-up; this one
    is the start-up a returning player has: the tab comes from `state.json`.
    """
    from PySide6.QtWidgets import QApplication

    from yulon import update_state
    from yulon.ui.controller_view import ControllerView

    monkeypatch.setenv("YULON_SMOKE_TEST", "1")  # no update check, no GitHub
    scratch = tmp_path / "config"
    scratch.mkdir()
    monkeypatch.setattr(
        update_state, "update_state_path", lambda config_dir=None: scratch / "update.json"
    )
    server_dir = tmp_path / "t210-server"
    monkeypatch.setattr(
        state,
        "load_state",
        lambda path=None, repair=True: state.AppState(
            installs=[state.KnownInstall(game="wow-wotlk", server_dir=server_dir)]
        ),
    )
    monkeypatch.setattr(state, "save_state", lambda app_state, path=None: None)
    monkeypatch.setattr(main, "sweep_leftover_client_copies", lambda **kwargs: None)
    real_init = ControllerView.__init__

    def _no_polling(self: Any, entry: Any, services: Any, **kwargs: Any) -> None:
        kwargs["status_poll_ms"] = 0
        real_init(self, entry, services, **kwargs)

    monkeypatch.setattr(ControllerView, "__init__", _no_polling)

    window = main.build_window()
    try:
        header = window.property("header")
        view = _tab_for(window, server_dir)
        assert window.property("tabs").currentWidget() is view
        assert header._badge.isHidden() is False, "the header hid the badge of the tab on screen"

        view.realm_badge.set_status("running")

        assert header._badge.status == "running"
        assert "REALM ONLINE" in header._badge._label.text()
    finally:
        main._stop_background_threads(window)
        QApplication.processEvents()


def test_the_header_says_unknown_when_docker_cannot_be_asked(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final fix round: the header follows the tab's badge into "unknown"."""
    from yulon import docker

    header = window.property("header")
    server_dir = tmp_path / "c6-unknown"
    _catalog_view(window).installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)

    def unreachable() -> object:
        raise docker.DockerCommandError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(view.services.controller, "status", unreachable)
    view.refresh_button.click()
    pump_until(lambda: header._badge.status == "unknown", "the header to say unknown")

    assert "UNKNOWN" in header._badge._label.text()


def test_the_header_badge_is_hidden_on_the_catalog(window: Any, tmp_path: Any) -> None:
    header = window.property("header")
    tabs = window.property("tabs")
    _catalog_view(window).installed.emit("wow-wotlk", tmp_path / "c6-catalog", None)
    assert header._badge.isHidden() is False

    tabs.setCurrentIndex(0)  # the Catalog

    assert tabs.tabText(0) == "Catalog"
    assert header._badge.isHidden() is True


def test_the_header_badge_follows_a_tab_rebuilt_under_it(window: Any, tmp_path: Any) -> None:
    """Review focus 2: Make…/Delete rebuild the tab; the old badge is gone with it."""
    header = window.property("header")
    server_dir = tmp_path / "c6-rebuilt"
    client = tmp_path / "TurtleWoW"
    play = tmp_path / "TurtleWoW (Yu'lon)"
    for folder in (client, play):
        (folder / "Interface").mkdir(parents=True)
    _catalog_view(window).installed.emit("wow-tortoise", server_dir, client)
    view = _tab_for(window, server_dir)
    view.services.set_play_client_dir(play)
    view.play_client_dir_changed.emit("wow-tortoise", server_dir, play)
    process_events()  # the old view's deferred delete

    made = _tab_for(window, server_dir)
    assert made is not view
    made.realm_badge.set_status("stopping")

    assert header._badge.status == "stopping"


def test_closing_the_tab_on_screen_leaves_the_header_on_what_is_left(
    window: Any, tmp_path: Any
) -> None:
    """Review focus 2: an uninstall drops the tab the header was following."""
    header = window.property("header")
    tabs = window.property("tabs")
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", tmp_path / "c6-kept", None)
    kept = _tab_for(window, tmp_path / "c6-kept")
    server_dir = tmp_path / "c6-closed"
    catalog.installed.emit("wow-wotlk", server_dir, None)
    closed = _tab_for(window, server_dir)
    kept.realm_badge.set_status("restarting")
    closed.realm_badge.set_status("running")
    tabs.setCurrentWidget(closed)
    assert tabs.indexOf(closed) == tabs.count() - 1, "the newest tab is the last one"
    assert header._badge.status == "running"

    closed.uninstalled.emit("wow-wotlk", server_dir)
    process_events()  # the dropped view's deferred delete

    # Qt selects the tab left of a removed last one: the one added just before.
    assert tabs.currentWidget() is kept
    assert header._badge.status == "restarting"
    assert header._badge.isHidden() is False
    kept.realm_badge.set_status("running")
    assert header._badge.status == "running", "the header stopped following"


# -- T188 A7/B7/C10: "&" in a label is shown, never read as a shortcut ---------


def _mnemonic_texts(window: Any) -> list[str]:
    """Every button text, group title and tab title in the window carrying a shortcut."""
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QAbstractButton, QGroupBox, QTabBar

    texts = [b.text() for b in window.findChildren(QAbstractButton)]
    texts += [g.title() for g in window.findChildren(QGroupBox)]
    for bar in window.findChildren(QTabBar):
        texts += [bar.tabText(i) for i in range(bar.count())]
    return [t for t in texts if not QKeySequence.mnemonic(t).isEmpty()]


def test_no_label_in_the_window_turns_a_letter_into_a_shortcut(window: Any, tmp_path: Any) -> None:
    """Audit A7/C10: "Console & Install Logs" drew as "Console _Install Logs"."""
    _catalog_view(window).installed.emit("wow-wotlk", tmp_path / "a7-sweep", None)

    assert _mnemonic_texts(window) == []


def test_a_server_folder_with_an_ampersand_keeps_it_on_its_tab(window: Any, tmp_path: Any) -> None:
    """B7: the tab title comes from the folder name, which nobody types for Qt."""
    from PySide6.QtGui import QKeySequence

    server_dir = tmp_path / "Raids & Dungeons"
    # T192: the folder is on the tab only when another tab has the same game,
    # so a second WotLK puts "Raids & Dungeons" on this one's second line.
    other = tmp_path / "Other WotLK"
    _catalog_view(window).installed.emit("wow-wotlk", server_dir, None)
    _catalog_view(window).installed.emit("wow-wotlk", other, None)
    try:
        view = _tab_for(window, server_dir)
        tabs = window.property("tabs")
        index = tabs.indexOf(view)

        title = tabs.tabText(index)
        assert "\n" in title, ("the folder is not on the tab", title)
        assert QKeySequence.mnemonic(title).isEmpty(), title
        assert "Raids & Dungeons" in title.replace("&&", "&"), title
        assert tabs.tabToolTip(index) == f"WoW WotLK — {server_dir}"
    finally:
        for folder in (server_dir, other):
            _tab_for(window, folder).uninstalled.emit("wow-wotlk", folder)
        process_events(10)


def test_remove_from_yulon_names_the_ready_to_play_client_it_leaves(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T181 fix round 1: the removal promises nothing is deleted, so the folder is named as left."""
    asked = _answer(monkeypatch, False)
    server_dir = tmp_path / "t181-remove"
    view, _stops = _removable_tab(window, monkeypatch, server_dir)
    play = tmp_path / "WoW (Yu'lon)"
    view.services.set_play_client_dir(play)
    view.play_client_dir_changed.emit("wow-tbc", server_dir, play)
    view = _tab_for(window, server_dir)

    view.forget_install_button.click()

    assert len(asked) == 1
    assert f"ready-to-play client at {play} is left where it is" in asked[0][1]


def test_the_removal_list_reads_the_other_installs_but_not_this_one_or_a_wsl_one(
    window: Any, tmp_path: Any
) -> None:
    """`other_server_dirs` is bound from the live state: every other install on this host."""
    catalog = _catalog_view(window)
    mine, other, wsl_one = tmp_path / "mine", tmp_path / "other", tmp_path / "in-wsl"
    catalog.installed.emit("wow-wotlk", mine, None)
    catalog.installed.emit("wow-tbc", other, None)
    catalog.adopted.emit("wow-vanilla", wsl_one, None, "Ubuntu-24.04")
    view = _tab_for(window, mine)

    assert view.services.other_server_dirs is not None
    others = view.services.other_server_dirs()
    # Membership, not equality: the window fixture's state may hold earlier installs.
    assert other in others
    assert mine not in others, "this server's own receipts would hide every file"
    assert wsl_one not in others, "reading it would boot its distro"


# ------------------------------------------------ T187: the client launcher window


@pytest.fixture
def launchers(window: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The launcher's reads inline, and every launcher a test opened closed after it."""
    from yulon.ui import launcher_window
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(launcher_window, "threaded_job_runner", lambda _parent: run_inline)
    yield window.yulon_launchers
    for launcher in list(window.yulon_launchers.values()):
        launcher.close()
        launcher.deleteLater()
    window.yulon_launchers.clear()
    process_events()


def _server(window: Any, tmp_path: Path, name: str, *, play: bool = False) -> Any:
    """A WotLK tab over its own folder, with a client folder, and a ready-to-play one if asked.

    The ready-to-play folder is recorded the way Make… records one: the view's
    signal, which `main.py` answers by rebuilding the tab over it.
    """
    server_dir = tmp_path / name
    client = tmp_path / f"{name}-client"
    client.mkdir(parents=True)
    _catalog_view(window).installed.emit("wow-wotlk", server_dir, client)
    view = _tab_for(window, server_dir)
    if play:
        play_dir = tmp_path / f"{name}-play"
        play_dir.mkdir()
        view.play_client_dir_changed.emit("wow-wotlk", server_dir, play_dir)
        view = _tab_for(window, server_dir)
        assert view.services.play_client_dir == play_dir
    return view


def _key(view: Any) -> tuple[str, Path]:
    return (view.entry.id, view.services.controller.server_dir)


def test_each_server_tab_has_a_play_beside_its_x_shown_with_it(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    """Spec 1: ▶ beside the T95 ×, on the hovered or current tab only, never a pad stop."""
    from PySide6.QtCore import Qt

    first = _server(window, tmp_path, "t187-first")
    second = _server(window, tmp_path, "t187-second")
    tabs = window.property("tabs")
    play_first, play_second = _play_of(window, first), _play_of(window, second)
    assert play_first is not None and play_first.parent() is _x_of(window, first).parent()
    assert play_first.text() == "▶"
    assert "launcher" in play_first.toolTip().casefold()
    assert tabs.currentWidget() is second
    assert play_first.isHidden() and not play_second.isHidden()
    _hover(tabs.tabBar(), tabs.indexOf(first))
    assert not play_first.isHidden()
    _leave(tabs.tabBar())
    assert play_first.isHidden()

    play_second.ensurePolished()
    assert play_second.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert play_second.maximumWidth() <= 18 and play_second.maximumHeight() <= 18
    strip = _strip_of(window, second)
    assert strip.geometry().width() <= tabs.tabBar().width(), "the strip is wider than the rail"


def test_the_sidebar_play_opens_the_servers_launcher_and_again_raises_the_same_one(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    from yulon.ui.launcher_window import LauncherWindow

    view = _server(window, tmp_path, "t187-open", play=True)
    _play_of(window, view).click()

    launcher = launchers[_key(view)]
    assert isinstance(launcher, LauncherWindow)
    assert launcher.isVisible() and launcher.view is view
    assert launcher.isWindow() and launcher.parent() is None, "not a window of its own"

    _play_of(window, view).click()
    assert launchers[_key(view)] is launcher and len(launchers) == 1

    launcher.close()
    _play_of(window, view).click()
    assert launchers[_key(view)] is launcher and launcher.isVisible(), "closed for good"


def test_one_launcher_per_server(window: Any, tmp_path: Path, launchers: Any) -> None:
    one = _server(window, tmp_path, "t187-one", play=True)
    two = _server(window, tmp_path, "t187-two", play=True)
    first = window.yulon_open_launcher(*_key(one))
    second = window.yulon_open_launcher(*_key(two))
    assert first is not second
    assert first.view is one and second.view is two


def test_the_server_tabs_play_opens_the_launcher_and_its_make_stays_a_make(
    window: Any, tmp_path: Path, launchers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 1 + 3: the tab keeps Play / Make / ▾; its Play opens the launcher, PLAY is in it."""
    from yulon.ui.controller_view import MAKE_PLAY_CLIENT_LABEL

    without = _server(window, tmp_path, "t187-make")
    made: list[int] = []
    monkeypatch.setattr(without, "make_play_client", lambda: made.append(1))
    assert without.play_button.text() == MAKE_PLAY_CLIENT_LABEL
    without.play_button.click()
    assert made == [1] and _key(without) not in launchers

    view = _server(window, tmp_path, "t187-tab-play", play=True)
    played: list[int] = []
    monkeypatch.setattr(view, "play", lambda: played.append(1))
    assert view.play_button.text() == "Play"
    view.play_button.click()

    assert played == [], "the tab played directly instead of opening the launcher"
    assert launchers[_key(view)].isVisible()
    assert not view.play_menu_button.isHidden(), "the ▾ menu went"
    from yulon.ui.theme import PLAY_MENU_BUTTON

    assert view.play_menu_button.objectName() == PLAY_MENU_BUTTON, "two arrows on the ▾"


def test_a_rebuilt_tab_is_followed_by_its_open_launcher(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    """After Make… or Delete `main.py` rebuilds the tab: the launcher drives the new one."""
    view = _server(window, tmp_path, "t187-rebuilt")
    launcher = window.yulon_open_launcher(*_key(view))
    assert launcher.view is view and not launcher.play_button.isEnabled()

    play_dir = tmp_path / "t187-rebuilt-play"
    play_dir.mkdir()
    view.play_client_dir_changed.emit("wow-wotlk", _key(view)[1], play_dir)
    process_events()

    rebuilt = _tab_for(window, _key(view)[1])
    assert rebuilt is not view
    assert launcher.view is rebuilt, "the launcher still drives the destroyed tab"
    assert launchers[_key(view)] is launcher
    assert rebuilt.open_launcher is not None


def test_removing_the_server_closes_its_launcher_and_forgets_its_place(
    window: Any, tmp_path: Path, launchers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 4: the × closes the launcher before the tab goes; nothing calls a dead view."""
    import shiboken6

    from yulon import ui_settings

    _answer(monkeypatch, True)
    view, _stops = _removable_tab(window, monkeypatch, tmp_path / "t187-remove", "wow-wotlk")
    key = _key(view)
    launcher = window.yulon_open_launcher(*key)
    ui_settings.remember_launcher(*key, addresses=["10.0.0.5"])

    _x_of(window, view).click()
    _collect_deleted()

    assert key not in launchers
    assert not shiboken6.isValid(launcher), "the closed launcher was never deleted"
    assert ui_settings.launcher_place(*key) == ui_settings.LauncherPlace()


def test_an_uninstall_closes_the_servers_launcher(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    view = _server(window, tmp_path, "t187-uninstall", play=True)
    key = _key(view)
    launcher = window.yulon_open_launcher(*key)
    closed: list[int] = []
    launcher.closed.connect(lambda: closed.append(1))

    view.uninstalled.emit(*key)

    assert closed == [1] and key not in launchers


def test_the_main_window_closing_closes_every_launcher(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    """App exit: a launcher left open would keep the app running with no main window."""
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication

    one = window.yulon_open_launcher(*_key(_server(window, tmp_path, "t187-exit-1")))
    two = window.yulon_open_launcher(*_key(_server(window, tmp_path, "t187-exit-2")))
    event = QCloseEvent()

    QApplication.sendEvent(window, event)

    assert event.isAccepted()
    assert one.isHidden() and two.isHidden()
    assert launchers == {}


def test_a_refused_close_leaves_the_launchers_open(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication

    class _Refuse(QObject):
        def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # noqa: N802
            if event.type() == QEvent.Type.Close:
                event.ignore()
                return True
            return False

    launcher = window.yulon_open_launcher(*_key(_server(window, tmp_path, "t187-refused")))
    guard = _Refuse()
    window.installEventFilter(guard)
    try:
        QApplication.sendEvent(window, QCloseEvent())
    finally:
        window.removeEventFilter(guard)

    assert launcher.isVisible()


def test_the_launchers_size_and_place_are_remembered_per_server(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    import shiboken6
    from PySide6.QtGui import QGuiApplication

    from yulon import ui_settings
    from yulon.ui.launcher_window import fitted_size

    view = _server(window, tmp_path, "t187-geometry")
    key = _key(view)
    launcher = window.yulon_open_launcher(*key)
    screen = QGuiApplication.primaryScreen().availableGeometry().size()
    assert launcher.size() == fitted_size(screen), "not the default size the first time"

    launcher.resize(1010, 702)
    process_events()
    launcher.close()
    assert ui_settings.launcher_place(*key).geometry, "nothing remembered at close"

    # As if Yu'lon were started again: no window for the server any more.
    launchers.pop(key).deleteLater()
    _collect_deleted()
    assert not shiboken6.isValid(launcher)
    again = window.yulon_open_launcher(*key)

    assert again is not launcher
    # The height as saved; the width is kept on the 800-wide offscreen screen, so
    # it comes back as the 960 minimum (see test_launcher_window's round trip).
    assert again.height() == 702 and again.width() == 960
    other = window.yulon_open_launcher(*_key(_server(window, tmp_path, "t187-geometry-2")))
    assert other.size() == fitted_size(screen), "another server's size was used"


def test_typed_addresses_are_remembered_per_server_and_offered_next_time(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    from yulon import ui_settings

    view = _server(window, tmp_path, "t187-addresses")
    key = _key(view)
    launcher = window.yulon_open_launcher(*key)

    launcher.addresses_changed.emit(["10.0.0.5", "lan.host"])

    assert ui_settings.launcher_place(*key).addresses == ["10.0.0.5", "lan.host"]
    launcher.close()
    launchers.pop(key).deleteLater()
    _collect_deleted()
    again = window.yulon_open_launcher(*key)
    offered = [again.realm_combo.itemText(i) for i in range(again.realm_combo.count())]
    assert "10.0.0.5" in offered and "lan.host" in offered


def test_the_launchers_server_tab_button_brings_its_tab_forward(
    window: Any, tmp_path: Path, launchers: Any
) -> None:
    view = _server(window, tmp_path, "t187-server-tab")
    other = _server(window, tmp_path, "t187-server-tab-other")
    launcher = window.yulon_open_launcher(*_key(view))
    tabs = window.property("tabs")
    assert tabs.currentWidget() is other

    launcher.server_tab_button.click()

    assert tabs.currentWidget() is view


# ------------------------------------------- temporary client copies (T179 Task 5)


def test_the_window_sweeps_leftover_client_copies_once_off_the_gui_thread(window: Any) -> None:
    """`trinitycore.remove_recorded_leftovers()` at start, never on the GUI thread.

    It removes folders of hard links to the player's own client, which can take a
    while on a slow disk; a notice is said when anything is left.
    """
    import threading

    from PySide6.QtWidgets import QApplication

    for _ in range(200):
        if window.swept:
            break
        QApplication.processEvents()
        threading.Event().wait(0.01)
    assert len(window.swept) == 1
    assert window.swept[0] != threading.main_thread().name
    for _ in range(200):
        if LEFTOVER_NOTICE_SEEN in window.bar_said:
            break
        QApplication.processEvents()
        threading.Event().wait(0.01)
    assert window.bar_said.count(LEFTOVER_NOTICE_SEEN) == 1
    # Shown -> recorded: the callback the bar is handed records THIS notice's folders.
    (on_shown,) = window.bar_callbacks
    recorded: list[Any] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main, "leftover_notice_shown", lambda notice, **kw: recorded.append(notice))
        on_shown()
    assert [notice.folders for notice in recorded] == [("/nowhere/a copy",)]


def test_the_sweep_says_nothing_when_nothing_is_left(tmp_path: Path) -> None:
    assert _REAL_SWEEP(config_dir=tmp_path) is None


def _leftover(tmp_path: Path, name: str) -> Path:
    """A folder at a noted place that is not ours: kept, warned about, noticed."""
    import json

    from yulon.catalog.families import trinitycore

    folder = tmp_path / name
    folder.mkdir()
    (folder / "keep.txt").write_text("not Yu'lon's", encoding="utf-8")
    listing = tmp_path / trinitycore.LEFTOVERS_FILE
    listed = json.loads(listing.read_text(encoding="utf-8")) if listing.is_file() else []
    listed.append({"target": str(folder), "game": "wow-centurion", "server_dir": str(tmp_path)})
    listing.write_text(json.dumps(listed), encoding="utf-8")
    return folder


def test_a_copy_that_stays_is_logged_and_said_in_one_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    foreign = _leftover(tmp_path, "WoW (Yu'lon map data, temporary)")
    with caplog.at_level("WARNING", logger="main"):
        notice = _REAL_SWEEP(config_dir=tmp_path)

    assert notice is not None and "\n" not in notice.text
    assert "temporary" in notice.text and "next time" in notice.text
    assert notice.folders == (str(foreign),)
    assert (foreign / "keep.txt").is_file(), "a folder that is not ours was touched"
    assert any(str(foreign) in record.getMessage() for record in caplog.records)


def test_a_notice_is_remembered_only_once_it_was_shown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T179 fix round 2: a notice that never reached the screen is said at the next start."""
    from yulon import ui_settings

    foreign = _leftover(tmp_path, "WoW (Yu'lon map data, temporary)")
    first = _REAL_SWEEP(config_dir=tmp_path)
    assert first is not None
    # Never shown (it waited behind an update offer the whole session): said again.
    again = _REAL_SWEEP(config_dir=tmp_path)
    assert again is not None and again.folders == (str(foreign),)

    main.leftover_notice_shown(again, config_dir=tmp_path)
    remembered = ui_settings.load_ui_settings(ui_settings.ui_settings_path(tmp_path))
    assert remembered.noticed_leftovers == [str(foreign)]
    caplog.clear()
    with caplog.at_level("WARNING", logger="main"):
        assert _REAL_SWEEP(config_dir=tmp_path) is None, "said again after it was shown"
    assert any(str(foreign) in record.getMessage() for record in caplog.records)

    other = _leftover(tmp_path, "WoW 2 (Yu'lon map data, temporary)")
    notice = _REAL_SWEEP(config_dir=tmp_path)
    assert notice is not None and notice.folders == (str(other),)
    assert "a temporary copy of a game client" in notice.text


# -- T193 A20: "Check for updates" is a real button, whole at every width -----------


@pytest.fixture
def shown_window(window: Any) -> Iterator[Any]:
    """The shared window shown on the Catalog, put back hidden at its size afterwards."""
    tabs = window.property("tabs")
    size, current = window.size(), tabs.currentIndex()
    tabs.setCurrentIndex(0)
    window.show()
    process_events(50)
    yield window
    window.hide()
    window.resize(size)
    window._restyle_for_width()
    tabs.setCurrentIndex(current)
    process_events(20)


def _at_width(window: Any, size: tuple[int, int]) -> None:
    """Resize the window and restyle it the way its settle timer does."""
    window.resize(*size)
    process_events(20)
    window._restyle_for_width()
    process_events(50)


def test_the_header_check_button_has_an_opaque_brass_edge(shown_window: Any) -> None:
    """A20: the header's selector-less sheet stripped the button's border and fill.

    `background: transparent; border: none;` with no selector reaches every
    child (T188 C1's mechanism), and the button was also flat: it read as a
    caption on the ember glow. Four pixels down the middle of its left edge are
    the theme's hairline, which is opaque, so the glow animating behind it
    cannot change them.

    Mutation: give the header its selector-less sheet back and the edge is the
    glow, not `COLOR_BRASS_DARK`.
    """
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QPushButton

    from yulon.ui.theme import COLOR_BRASS_DARK

    button = shown_window.findChild(QPushButton, "check-for-updates")
    assert button is not None
    button.clearFocus()
    image = button.grab().toImage()
    middle = image.height() // 2
    edge = [image.pixelColor(0, y).name() for y in range(middle - 2, middle + 2)]
    assert edge == [QColor(COLOR_BRASS_DARK).name()] * 4, edge


@pytest.mark.parametrize("size", [(960, 640), (1280, 800), (1920, 1080)])
def test_the_header_check_button_is_never_cut_off(shown_window: Any, size: tuple[int, int]) -> None:
    """T188's renders showed "Check for updates" cut at 1280 on the Catalog.

    Measured for T193: a harness artefact, not the app. The shot was taken 0.2 s
    after the resize, and the 120 ms restyle had grown the font but the header's
    relayout had not landed yet; at a 1 s settle the button is whole at every
    size. Kept as the guard on that answer: reached from 960, the way a window
    is dragged wider, the button is as wide as it asks to be, wider than its words.
    """
    from PySide6.QtWidgets import QPushButton

    from yulon.ui.theme import TOUCH_TARGET_PX

    button = shown_window.findChild(QPushButton, "check-for-updates")
    assert button is not None
    _at_width(shown_window, (960, 640))
    _at_width(shown_window, size)

    words = button.fontMetrics().horizontalAdvance(button.text())
    assert button.width() >= button.sizeHint().width(), (button.width(), button.sizeHint())
    assert button.width() > words, (button.width(), words)
    # And inside the header, top to bottom: at the theme's 8px padding it was
    # 50px tall in a 44px row and stood out over the header's bottom edge.
    header = shown_window.property("header")
    inside = header.contentsRect().marginsRemoved(header.layout().contentsMargins())
    assert inside.contains(button.geometry()), (button.geometry(), inside)
    assert button.height() >= TOUCH_TARGET_PX


# -- T192: whole short names on the rail; Catalog and Logs pinned above it -----------


@pytest.fixture
def five_servers(shown_window: Any, tmp_path: Any) -> Iterator[list[Any]]:
    """Five server tabs, the second WotLK on another disk with the same folder name.

    Removed again afterwards through the tab's own `uninstalled`, as every test
    on the shared window must.
    """
    made = [
        ("wow-tbc", tmp_path / "WoW TBC"),
        ("wow-vanilla", tmp_path / "WoW Vanilla"),
        ("wow-tortoise", tmp_path / "WoW Tortoise"),
        ("wow-centurion", tmp_path / "WoW Centurion"),
        ("wow-wotlk", tmp_path / "disk2" / "WoW WotLK"),
    ]
    catalog = _catalog_view(shown_window)
    for game, folder in made:
        catalog.installed.emit(game, folder, None)
    process_events(30)
    yield [_tab_for(shown_window, folder) for _, folder in made]
    for game, folder in made:
        view = _tab_for(shown_window, folder)
        view.uninstalled.emit(game, folder)
    process_events(20)


SIZES = [(960, 640), (1280, 800), (1920, 1080)]


@pytest.mark.parametrize("size", SIZES)
def test_every_server_tab_shows_its_whole_short_name(
    shown_window: Any, five_servers: list[Any], size: tuple[int, int]
) -> None:
    """A3/B5/C9: with more tabs than fit, every tab shrank to "WoW…" / "Cata…".

    Short names ("TBC", not "WoW TBC — WoW TBC"), never elided: the rail
    scrolls instead, so each tab is at least as long as its text asks.

    Mutation: `ElideRight` back and the 960 tabs are shorter than their hints.
    """
    _at_width(shown_window, size)
    tabs = shown_window.property("tabs")
    bar = tabs.tabBar()
    for view in five_servers:
        index = tabs.indexOf(view)
        assert (
            bar.tabRect(index).height() >= bar.tabSizeHint(index).height()
        ), f"{bar.tabText(index)!r} at {size}: {bar.tabRect(index)} < {bar.tabSizeHint(index)}"
        assert not bar.tabText(index).startswith("WoW "), bar.tabText(index)
    # Its first line, whatever other TBC tab an earlier test on the shared
    # window left open (that one would add the folder as a second line).
    assert bar.tabText(tabs.indexOf(five_servers[0])).split("\n")[0] == "TBC"


@pytest.mark.parametrize("size", SIZES)
def test_catalog_and_logs_are_pinned_above_the_rail_at_every_size(
    shown_window: Any, five_servers: list[Any], size: tuple[int, int]
) -> None:
    """The Catalog was tab 0 of a scrolling bar, so with a few servers it scrolled away.

    Both are pinned buttons above the bar, inside the tab widget, each at least
    its own size hint, and the bar starts below them -- after the restyle at
    each size, which is what moves the bar.

    Mutation: drop the `::tab-bar` offset and the bar starts at the top, under
    the pins.
    """
    _at_width(shown_window, size)
    tabs = shown_window.property("tabs")
    bar = tabs.tabBar()
    pins = shown_window.yulon_sidebar_pins
    assert pins.isVisible()
    assert tabs.rect().contains(pins.geometry()), (pins.geometry(), tabs.rect())
    assert pins.geometry().bottom() < bar.geometry().top(), (pins.geometry(), bar.geometry())
    for button in pins.buttons.values():
        assert button.isVisible()
        assert button.width() >= button.sizeHint().width(), (button.size(), button.sizeHint())
        assert button.height() >= button.sizeHint().height(), (button.size(), button.sizeHint())
    assert not bar.isTabVisible(0) and not bar.isTabVisible(1)


def test_the_catalog_pin_stays_put_and_visible_with_the_last_server_current(
    shown_window: Any, five_servers: list[Any]
) -> None:
    _at_width(shown_window, (960, 640))
    tabs = shown_window.property("tabs")
    pins = shown_window.yulon_sidebar_pins
    catalog = pins.buttons[0]
    tabs.setCurrentIndex(0)
    process_events(20)
    where = catalog.mapTo(shown_window, catalog.rect().topLeft())

    tabs.setCurrentWidget(five_servers[-1])
    process_events(20)

    assert catalog.isVisible()
    assert catalog.mapTo(shown_window, catalog.rect().topLeft()) == where
    assert not catalog.isChecked() and not pins.buttons[1].isChecked()


def test_pressing_the_catalog_pin_shows_the_catalog(
    shown_window: Any, five_servers: list[Any]
) -> None:
    """The pin is the Catalog's tab now: it shows tab 0, is checked, and the header
    hides the realm badge as it does on the Catalog (T188 C6)."""
    tabs = shown_window.property("tabs")
    header = shown_window.property("header")
    pins = shown_window.yulon_sidebar_pins
    tabs.setCurrentWidget(five_servers[-1])
    process_events(10)
    assert header._badge.isHidden() is False

    pins.buttons[0].click()
    process_events(10)

    assert tabs.currentIndex() == 0
    assert pins.buttons[0].isChecked() and not pins.buttons[1].isChecked()
    assert header._badge.isHidden() is True

    pins.buttons[1].click()
    process_events(10)
    assert tabs.currentIndex() == 1
    assert pins.buttons[1].isChecked() and not pins.buttons[0].isChecked()


def test_the_next_tab_bumper_from_the_last_server_lands_on_the_catalog(
    shown_window: Any, five_servers: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """LB/RB still walk tabs 0..n (they stayed tabs), and the pin follows them.

    The bumper acts on the tab widget around the FOCUSED widget. On the shared
    window, after the module's earlier tests, offscreen Qt will not make this
    window active again (measured: `requestActivate()` never lands), so the
    focus is answered as the rail's bar -- what a pad user on the rail has.
    """
    from PySide6.QtWidgets import QApplication

    from yulon.ui import gamepad
    from yulon.ui.gamepad import Action

    tabs = shown_window.property("tabs")
    pins = shown_window.yulon_sidebar_pins
    tabs.setCurrentIndex(tabs.count() - 1)
    monkeypatch.setattr(gamepad.QApplication, "focusWidget", staticmethod(tabs.tabBar))
    assert QApplication.focusWidget() is tabs.tabBar()
    process_events(10)

    shown_window.yulon_keyboard._navigator.perform(Action.CYCLE_NEXT)
    process_events(10)

    assert tabs.currentIndex() == 0
    assert pins.buttons[0].isChecked()


# -- T221: R and L in a list are letters, on every game's server page ----------------


@pytest.mark.parametrize(
    "game", ["wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"]
)
def test_r_and_l_in_every_list_of_a_server_page_stay_in_the_list(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, game: str
) -> None:
    """T221: R in Centurion's Characters list switched the server page to Bots.

    Through the window's own keyboard filter, with each list of each sub-tab as
    the focus: the filter must hand R and L to the list (False) and leave the
    sub-tab where it is. The focus is answered by patch for the reason the
    bumper test above gives: offscreen, the shared window is never active again.
    """
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QAbstractItemView

    from yulon.ui import gamepad

    server_dir = tmp_path / f"t221-{game}"
    _catalog_view(window).installed.emit(game, server_dir, None)
    view = _tab_for(window, server_dir)
    sub = view._tabs
    keyboard = window.yulon_keyboard
    asked: list[str] = []
    for index in range(sub.count()):
        sub.setCurrentIndex(index)
        page = sub.widget(index)
        for rows in page.findChildren(QAbstractItemView):
            monkeypatch.setattr(gamepad.QApplication, "focusWidget", staticmethod(lambda r=rows: r))
            for key, letter in ((Qt.Key.Key_R, "r"), (Qt.Key.Key_L, "l")):
                press = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, letter)
                where = f"{sub.tabText(index)}: {type(rows).__name__} {rows.objectName()!r}"
                assert keyboard.eventFilter(rows, press) is False, f"{letter} was taken in {where}"
                assert sub.currentIndex() == index, f"{letter} switched sub-tab from {where}"
            if rows.isVisibleTo(page):
                asked.append(sub.tabText(index))
    if game == "wow-centurion":
        assert "Characters" in asked, "the Characters list was not asked"
    assert asked, "no list on any sub-tab was asked"


# -- T192: a status dot on each server tab; the server's name in the header -----------


def _tab_dot(window: Any, view: Any) -> Any:
    """The tab's icon as the rail draws it, at the rail's icon size."""
    tabs = window.property("tabs")
    size = tabs.iconSize()
    return tabs.tabIcon(tabs.indexOf(view)).pixmap(size).toImage()


def _dot_is(image: Any, hex_colour: str) -> bool:
    """The dot's centre pixel is `hex_colour` (within 24 a channel), opaque."""
    from PySide6.QtGui import QColor

    from yulon.ui.sidebar import status_dot_geometry

    centre, _radius, _halo = status_dot_geometry(image.width())
    got, want = image.pixelColor(int(centre.x()), int(centre.y())), QColor(hex_colour)
    return got.alpha() > 200 and all(
        abs(a - b) <= 24 for a, b in zip(got.getRgb()[:3], want.getRgb()[:3], strict=True)
    )


def _drop(window: Any, game: str, server_dir: Path) -> None:
    for view in list(window.yulon_controllers):
        if view.services.controller.server_dir == server_dir:
            view.uninstalled.emit(game, server_dir)
    process_events(10)


def test_a_server_tab_carries_a_dot_that_follows_its_realm_badge(
    window: Any, tmp_path: Any
) -> None:
    """C28: every server tab had the same icon, so the rail said nothing about which were up.

    Mutation: leave `status_changed` unconnected and the dot stays at the
    status the tab was built with.
    """
    from yulon.ui.icons import get_tab_icon
    from yulon.ui.theme import COLOR_GOLD_BRIGHT, COLOR_RARE, COLOR_UNCOMMON

    server_dir = tmp_path / "t192-dot"
    _catalog_view(window).installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)
    try:
        view.realm_badge.set_status("running")
        assert _dot_is(_tab_dot(window, view), COLOR_UNCOMMON)
        view.realm_badge.set_status("restarting")
        assert _dot_is(_tab_dot(window, view), COLOR_RARE)
        view.realm_badge.set_status("partial")
        assert _dot_is(_tab_dot(window, view), COLOR_GOLD_BRIGHT)

        # T188: Docker did not answer. No dot -- a ring would claim it is offline.
        view.realm_badge.set_status("unknown")
        plain = get_tab_icon("server").pixmap(window.property("tabs").iconSize()).toImage()
        dot = _tab_dot(window, view)
        assert dot.convertToFormat(plain.format()) == plain, "an unknown realm shows a dot"
    finally:
        _drop(window, "wow-wotlk", server_dir)


def test_a_new_server_tab_starts_with_the_dot_of_its_badge(window: Any, tmp_path: Any) -> None:
    """Read at `add_controller`, not only on the first change: the badge starts stopped."""
    from yulon.ui.theme import COLOR_TEXT_MUTED, COLOR_UNCOMMON

    server_dir = tmp_path / "t192-first-dot"
    _catalog_view(window).installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)
    try:
        assert view.realm_badge.status == "stopped"
        image = _tab_dot(window, view)
        assert not _dot_is(image, COLOR_UNCOMMON)
        from PySide6.QtGui import QColor

        from yulon.ui.sidebar import status_dot_geometry

        centre, radius, _halo = status_dot_geometry(image.width())
        muted = QColor(COLOR_TEXT_MUTED).getRgb()[:3]
        ring = [
            image.pixelColor(x, y)
            for y in range(image.height())
            for x in range(image.width())
            if abs(((x + 0.5 - centre.x()) ** 2 + (y + 0.5 - centre.y()) ** 2) ** 0.5 - radius)
            < 1.5
        ]
        assert any(
            c.alpha() > 200
            and all(abs(a - b) <= 24 for a, b in zip(c.getRgb()[:3], muted, strict=True))
            for c in ring
        ), "a new tab shows no stopped ring"
    finally:
        _drop(window, "wow-wotlk", server_dir)


def test_the_dot_shows_the_badge_held_while_our_stop_runs(
    window: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T188 holds the badge at "stopping" while our own Stop runs; the dot holds with it."""
    from yulon.ui.theme import COLOR_GOLD_BRIGHT, COLOR_UNCOMMON

    server_dir = tmp_path / "t192-held"
    _catalog_view(window).installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(window, server_dir)
    try:
        view.realm_badge.set_status("running")
        assert _dot_is(_tab_dot(window, view), COLOR_UNCOMMON)
        monkeypatch.setattr(view, "_run", lambda *_args, **_kwargs: None)

        view.stop_server()

        assert view.realm_badge.status == "stopping"
        assert _dot_is(_tab_dot(window, view), COLOR_GOLD_BRIGHT)
    finally:
        _drop(window, "wow-wotlk", server_dir)


def test_a_rebuilt_tab_gets_its_new_badge_s_dot_and_the_old_badge_lets_go(
    window: Any, tmp_path: Any
) -> None:
    """A new client folder rebuilds the tab (`on_client_dir_changed`): a new view, a new badge.

    The old badge must not keep a line to the tab: nothing is left listening
    to it, and what it says no longer reaches the rail.

    Mutation: leave the old badge connected and it still has a receiver.
    """
    from PySide6.QtCore import SIGNAL

    from yulon.ui.theme import COLOR_RARE, COLOR_UNCOMMON

    server_dir = tmp_path / "t192-rebuilt"
    client = tmp_path / "t192-client"
    (client / "Data").mkdir(parents=True)
    _catalog_view(window).installed.emit("wow-wotlk", server_dir, None)
    old = _tab_for(window, server_dir)
    try:
        old.client_dir_changed.emit("wow-wotlk", server_dir, client)
        made = _tab_for(window, server_dir)
        assert made is not old

        made.realm_badge.set_status("running")
        assert _dot_is(_tab_dot(window, made), COLOR_UNCOMMON)
        assert old.realm_badge.receivers(SIGNAL("status_changed(QString)")) == 0
        old.realm_badge.set_status("restarting")  # still alive until its deferred delete
        assert not _dot_is(_tab_dot(window, made), COLOR_RARE)
        process_events()  # the old view's deferred delete
        made.realm_badge.set_status("restarting")
        assert _dot_is(_tab_dot(window, made), COLOR_RARE)
    finally:
        _drop(window, "wow-wotlk", server_dir)


def test_the_header_names_the_server_on_screen_and_nothing_on_the_catalog(
    shown_window: Any, tmp_path: Any
) -> None:
    """The game strip: "WoW WotLK — <folder>" beside the badge, clear of both header controls.

    Mutation: stop passing `title=` and the header names nothing.
    """
    from PySide6.QtWidgets import QPushButton

    header = shown_window.property("header")
    tabs = shown_window.property("tabs")
    server_dir = tmp_path / "Hdr Realm"
    _catalog_view(shown_window).installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(shown_window, server_dir)
    try:
        tabs.setCurrentWidget(view)
        _at_width(shown_window, (960, 640))
        title = header._realm_title
        check = shown_window.findChild(QPushButton, "check-for-updates")
        assert title.isVisible()
        assert title.text().startswith("WoW WotLK"), title.text()
        assert title.toolTip() == f"WoW WotLK — {server_dir}"
        assert not title.geometry().intersects(check.geometry()), (
            title.geometry(),
            check.geometry(),
        )
        assert not title.geometry().intersects(header._badge.geometry())
        assert check.width() >= check.sizeHint().width(), "the name squeezed the update check"

        tabs.setCurrentIndex(0)
        process_events(10)
        assert title.isHidden(), "the Catalog names a server"
        tabs.setCurrentIndex(1)
        process_events(10)
        assert title.isHidden(), "Logs names a server"
    finally:
        _drop(shown_window, "wow-wotlk", server_dir)


def test_the_header_title_shortens_when_the_tab_that_needed_the_longer_one_goes(
    window: Any, tmp_path: Any
) -> None:
    """Two "Hdr Twin" folders need a parent folder each to tell apart; one alone does not.

    Removing a tab that is not on screen moves no selection, so it is the
    retitle after the removal that has to refresh the header.
    """
    header = window.property("header")
    tabs = window.property("tabs")
    first, second = tmp_path / "d1" / "Hdr Twin", tmp_path / "d2" / "Hdr Twin"
    catalog = _catalog_view(window)
    catalog.installed.emit("wow-wotlk", first, None)
    catalog.installed.emit("wow-wotlk", second, None)
    try:
        tabs.setCurrentWidget(_tab_for(window, first))
        assert header._realm_title.text() == f"WoW WotLK — {Path('d1') / 'Hdr Twin'}"

        _drop(window, "wow-wotlk", second)

        assert header._realm_title.text() == "WoW WotLK — Hdr Twin"
    finally:
        _drop(window, "wow-wotlk", first)
        _drop(window, "wow-wotlk", second)


def test_ctrl_tab_reaches_the_catalog_and_logs_from_the_rail_and_from_them(
    shown_window: Any, five_servers: list[Any]
) -> None:
    """The changelog's claim, in the real window (T192 final review).

    From the rail, the Catalog or Logs, Ctrl+Tab and Ctrl+Shift+Tab walk the
    same ring as the bumpers, pinned tabs included, and the pins follow. Inside
    a server page its own sub-tabs take the keys first and the rail does not
    move: cycling a server's sub-tabs is that page's natural Ctrl+Tab.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QTabWidget

    tabs = shown_window.property("tabs")
    pins = shown_window.yulon_sidebar_pins
    last = tabs.count() - 1
    ctrl = Qt.KeyboardModifier.ControlModifier
    back = ctrl | Qt.KeyboardModifier.ShiftModifier

    def press(widget: Any, key: Qt.Key, modifiers: Qt.KeyboardModifier) -> int:
        assert widget.isVisible(), widget
        QTest.keyClick(widget, key, modifiers)
        process_events(10)
        return tabs.currentIndex()

    tabs.setCurrentIndex(last)
    process_events(10)
    assert press(tabs.tabBar(), Qt.Key.Key_Tab, ctrl) == 0, "the rail skipped the Catalog"
    assert pins.buttons[0].isChecked()
    catalog = _catalog_view(shown_window)
    assert press(catalog, Qt.Key.Key_Tab, ctrl) == 1, "the Catalog skipped Logs"
    assert pins.buttons[1].isChecked() and not pins.buttons[0].isChecked()
    assert press(shown_window.yulon_logs_view, Qt.Key.Key_Backtab, back) == 0
    assert pins.buttons[0].isChecked()
    assert press(catalog, Qt.Key.Key_Backtab, back) == last, "Ctrl+Shift+Tab did not wrap"
    assert not any(button.isChecked() for button in pins.buttons.values())

    server = five_servers[-1]
    tabs.setCurrentWidget(server)
    process_events(10)
    inner = server.findChild(QTabWidget)
    assert inner is not None
    before = inner.currentIndex()
    assert press(inner.currentWidget(), Qt.Key.Key_Tab, ctrl) == tabs.indexOf(server)
    assert (
        inner.currentIndex() == (before + 1) % inner.count()
    ), "the server's sub-tabs did not move"


def test_a_long_server_name_is_shortened_in_a_narrow_header_and_whole_again_wider(
    shown_window: Any, tmp_path: Any
) -> None:
    """The header's name gives way before the update check and the badge, and comes back.

    Task 2's lesson: a label whose size hint is what it shows never asks for
    its room back, so a name cut at 960 stayed cut at 1920.

    Mutation: answer `_RealmTitle.sizeHint` from the text on screen and the
    name is still shortened at 1920.
    """
    from PySide6.QtWidgets import QPushButton

    header = shown_window.property("header")
    tabs = shown_window.property("tabs")
    server_dir = tmp_path / "A very long server folder name that cannot fit beside the badge"
    _catalog_view(shown_window).installed.emit("wow-wotlk", server_dir, None)
    view = _tab_for(shown_window, server_dir)
    try:
        tabs.setCurrentWidget(view)
        title = header._realm_title
        check = shown_window.findChild(QPushButton, "check-for-updates")
        _at_width(shown_window, (960, 640))
        whole = title.full_text
        assert whole == f"WoW WotLK — {server_dir.name}"
        assert title.text() != whole and title.text().endswith("…"), title.text()
        assert title.text().startswith("WoW"), title.text()
        assert not title.geometry().intersects(check.geometry())
        assert not title.geometry().intersects(header._badge.geometry())
        assert check.width() >= check.sizeHint().width(), "the name squeezed the update check"

        _at_width(shown_window, (1920, 1080))
        assert title.text() == whole, f"still cut at 1920: {title.text()!r}"
        assert not title.geometry().intersects(check.geometry())
        assert not title.geometry().intersects(header._badge.geometry())
    finally:
        _drop(shown_window, "wow-wotlk", server_dir)


# ------------------------------------------------- what a player reads (T194)

_FIVE_GAMES = ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion")


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Pressed live on the test box (T86).", "ticket number"),
        ("Scope is an owner decision, 2026-09-06.", "owner decision"),
        ("Module management is Manifest-driven.", "manifest"),
        ("It carries the headless bot sessions.", "headless"),
        ("RuntimeError: port in use", "exception class"),
        ("Traceback (most recent call last):", "traceback"),
        ("open //./pipe/dockerDesktopLinuxEngine failed", "pipe path"),
        ("the npipe transport is closed", "npipe"),
    ],
)
def test_the_sweep_names_each_kind_of_developer_note(qapp: object, text: str, rule: str) -> None:
    """Each sentence breaks exactly one rule, and the fault names it and the widget."""
    from PySide6.QtWidgets import QLabel, QWidget

    from tests.support_player_text import player_text_faults

    root = QWidget()
    QLabel(text, root).setObjectName("probe")

    assert player_text_faults(root) == [f"probe text [{rule}]: {text!r}"]


def test_the_sweep_reads_every_place_text_is_shown_and_skips_what_is_not(qapp: object) -> None:
    """T1-T7 are read where a player sees them; T8-T11 are not text Yu'lon shows them.

    T8 is typed by the player, T9 is on a hidden widget, T10 is in the install
    console and T11 is a line of a log file in the Logs viewer.
    """
    import re

    from PySide6.QtWidgets import (
        QGroupBox,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    from tests.support_player_text import player_text_faults
    from yulon.catalog.catalog import load_catalog
    from yulon.ui.logs_view import LogsView
    from yulon.ui.widgets.log_panel import LogPanel

    root = QWidget()
    lay = QVBoxLayout(root)
    lay.addWidget(QPushButton("T1 on a button"))
    tipped = QPushButton("Start")
    tipped.setToolTip("T2 in a tooltip")
    lay.addWidget(tipped)
    lay.addWidget(QGroupBox("T3 as a group title"))
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "T4 on a tab")
    tabs.tabBar().setTabToolTip(0, "T5 on a tab's tooltip")
    lay.addWidget(tabs)
    shown = QPlainTextEdit("T6 in a read-only box")
    shown.setReadOnly(True)
    lay.addWidget(shown)
    hint = QPlainTextEdit()
    hint.setReadOnly(True)
    hint.setPlaceholderText("T7 as a placeholder")
    lay.addWidget(hint)
    lay.addWidget(QPlainTextEdit("T8 typed by the player"))
    hidden = QLabel("T9 on a hidden label")
    lay.addWidget(hidden)
    hidden.setVisible(False)
    console = LogPanel()
    QLabel("T10 inside the install console", console)
    lay.addWidget(console)
    logs = LogsView(lambda: (), load_catalog(), jobs=lambda *_a: None)
    logs.viewer.setPlainText("T11 a line of a log file")
    lay.addWidget(logs)

    found = {
        match.group(0)
        for fault in player_text_faults(root)
        if (match := re.search(r"T\d+", fault)) is not None
    }

    assert found == {"T1", "T2", "T3", "T4", "T5", "T6", "T7"}


@pytest.fixture
def one_of_each_game(window: Any, tmp_path: Any) -> Iterator[dict[str, Any]]:
    """A tab for each of the five games, dropped again afterwards."""
    dirs = {game: tmp_path / f"t194-{game}" for game in _FIVE_GAMES}
    for game, server_dir in dirs.items():
        _catalog_view(window).installed.emit(game, server_dir, None)
    tabs = window.property("tabs")
    current = tabs.currentIndex()
    try:
        yield {game: _tab_for(window, server_dir) for game, server_dir in dirs.items()}
    finally:
        tabs.setCurrentIndex(current)
        for game, server_dir in dirs.items():
            _drop(window, game, server_dir)


def _every_screen(window: Any, views: dict[str, Any]) -> Iterator[str]:
    """Make each screen current in turn: the Catalog, the Logs, every server's every sub-tab."""
    tabs = window.property("tabs")
    logs_view = window.yulon_logs_view
    for index in range(tabs.count()):
        page = tabs.widget(index)
        if page is logs_view or page.isAncestorOf(logs_view):
            tabs.setCurrentIndex(index)
            yield "Logs"
    tabs.setCurrentWidget(_catalog_view(window))
    if tabs.currentWidget() is not _catalog_view(window):
        tabs.setCurrentIndex(0)
    yield "Catalog"
    for game, view in views.items():
        tabs.setCurrentWidget(view)
        for index in range(view._tabs.count()):
            view._tabs.setCurrentIndex(index)
            yield f"{game} {view._tabs.tabText(index)}"


def test_no_screen_shows_a_player_a_developer_note(window: Any, one_of_each_game: Any) -> None:
    """A17/I1, I2, C19, C25, A25, C20 (T194): every screen, all five games, read in full.

    Ticket numbers, owner decisions, "manifest", "headless", exception class
    names, tracebacks and Docker pipe paths are notes to ourselves. They live in
    comments, docstrings and `CatalogEntry.notes`, never on screen.
    """
    from tests.support_player_text import player_text_faults

    faults: list[str] = []
    for screen in _every_screen(window, one_of_each_game):
        process_events(5)
        faults += [f"{screen}: {fault}" for fault in player_text_faults(window)]

    faults = list(dict.fromkeys(faults))
    assert faults == [], "\n".join(faults)


def test_the_catalogs_developer_notes_load_and_are_never_drawn(
    window: Any, one_of_each_game: Any
) -> None:
    """C19 (T194): what moved out of the descriptions is kept beside the entry, not shown."""
    from tests.support_player_text import visible_texts
    from yulon.catalog.catalog import CATALOG_FILE, load_catalog

    written = {
        game["id"]: tuple(game.get("notes", ()))
        for game in json.loads(CATALOG_FILE.read_text(encoding="utf-8"))["games"]
    }
    loaded = {entry.id: entry.notes for entry in load_catalog().games}
    assert loaded == written
    assert written["wow-tortoise"], "the Tortoise description's history was not kept"

    shown: list[str] = []
    for _screen in _every_screen(window, one_of_each_game):
        shown += [text for _name, _where, text in visible_texts(window)]
    drawn = [
        (game, note)
        for game, notes in loaded.items()
        for note in notes
        if any(note[:60] in text for text in shown)
    ]
    assert drawn == []


def test_the_bot_party_sentence_on_tortoise_names_wotlk_and_no_decision(
    one_of_each_game: Any,
) -> None:
    """A17/I1 (T194): the reason is the player's, not the meeting that settled it."""
    absent = one_of_each_game["wow-tortoise"].my_party_absent

    assert not absent.isHidden()
    assert "WoW WotLK" in absent.text(), absent.text()
    assert "owner decision" not in absent.text(), absent.text()
    assert "mod-ale" not in absent.text(), absent.text()


def test_the_tortoise_set_level_sentence_is_short_and_plain(one_of_each_game: Any) -> None:
    """I2 (T194): it was 470 characters of console commands in backticks."""
    said = one_of_each_game["wow-tortoise"].set_level_absent

    assert not said.isHidden()
    assert said.text(), "the sentence is gone, not shortened"
    assert len(said.text()) < 200, len(said.text())
    assert "`" not in said.text(), said.text()


# -- T195 C23/C24/C31: the first run, the tiles, the strip --------------------------


def _catalog_console(window: Any) -> tuple[Any, Any, Any]:
    """The Catalog's install-log tabs, its toggle, and the splitter between them."""
    from PySide6.QtWidgets import QSplitter, QTabWidget

    console = window.findChild(QTabWidget, "catalog-console-tabs")
    splitter = window.findChild(QSplitter, "catalog-splitter")
    assert console is not None and splitter is not None
    return console, _catalog_view(window).toggle_console_button, splitter


def test_the_first_run_catalog_says_what_to_do_and_hides_the_empty_log(
    shown_window: Any,
) -> None:
    """C23 (T195): a heading that asks, a line that promises, and no empty console.

    The console was open at start with nothing in it, its tab text clipped to
    "and Ins", and it took the room the Install buttons needed at 960.
    Mutation: leave the console visible at start and `isHidden()` is False.
    """
    from PySide6.QtWidgets import QLabel

    view = _catalog_view(shown_window)
    heading = view.findChild(QLabel, "section-title")
    welcome = view.findChild(QLabel, "catalog-welcome")
    assert heading is not None and heading.text() == "Choose a game to install"
    assert welcome is not None and welcome.isVisibleTo(shown_window)
    assert welcome.text() == (
        "Yu'lon downloads and builds the server for you. Nothing changes on this "
        "computer until you press Install."
    )
    console, toggle, _splitter = _catalog_console(shown_window)
    assert console.isHidden() and toggle.isHidden()


def test_an_install_start_opens_the_log_at_a_readable_height(shown_window: Any) -> None:
    """C23/C31 (T195): the first `run_started` shows the log and its toggle, 55/45.

    The console used to open 170 px tall whatever the window, under a strip that
    then had no room to say anything.
    Mutation: the old `setSizes([h - 170, 170])` leaves it under 40% at 1280x800.
    """
    console, toggle, splitter = _catalog_console(shown_window)
    panel = shown_window.yulon_log_panels[0]
    was_hidden = console.isHidden(), toggle.isHidden()
    _at_width(shown_window, (1280, 800))
    try:
        panel.run_started.emit()
        process_events(30)
        assert not console.isHidden() and not toggle.isHidden()
        assert console.tabText(0) == "Install log"
        top, bottom = splitter.sizes()
        assert bottom >= 0.4 * (top + bottom), splitter.sizes()
        assert toggle.text() == "▼ Hide install log"
    finally:
        # Put back as the app had them, not forced hidden: a forced hide would
        # give the next test the first-run state whatever the app does.
        console.setHidden(was_hidden[0])
        toggle.setHidden(was_hidden[1])
        process_events(10)


def test_at_960_the_first_rows_install_buttons_are_in_view(shown_window: Any) -> None:
    """C23 (T195): at the smallest window, Install is on the first frame, not below it.

    The window as the app leaves it with no install run yet: with the empty
    console open under the shelf (the old layout), WotLK's Install sat below
    the fold at 960x640. Mutation: show the console at start and this fails.
    """
    from PySide6.QtWidgets import QScrollArea

    view = _catalog_view(shown_window)
    _at_width(shown_window, (960, 640))
    assert (shown_window.width(), shown_window.height()) == (960, 640)
    viewport = view.findChild(QScrollArea, "catalog-shelf-scroll").viewport()
    below = []
    for game in ("wow-wotlk", "wow-tbc"):
        button = view.button_for(game)
        bottom = button.mapTo(viewport, button.rect().bottomLeft()).y()
        if bottom > viewport.height():
            below.append(f"{game}: bottom {bottom} of {viewport.height()}")
    assert below == []


def test_at_1920_no_tile_is_taller_than_it_asks_to_be(shown_window: Any) -> None:
    """C24 (T195): a one-line description made a ~370 px tile (its box had stretch 1)."""
    from PySide6.QtWidgets import QWidget

    from yulon.catalog.catalog import load_catalog

    view = _catalog_view(shown_window)
    _at_width(shown_window, (1920, 1080))
    tall = []
    for entry in load_catalog().games:
        tile = view.findChild(QWidget, f"catalog-tile-{entry.id}")
        assert tile is not None, entry.id
        if tile.height() > tile.sizeHint().height() + 1:
            tall.append(f"{entry.id}: {tile.height()} > {tile.sizeHint().height()}")
    assert tall == []


def test_at_1920_the_strip_says_the_whole_step(shown_window: Any) -> None:
    """C31 (T195): the step field was capped at 200 px and cut "Compiling the wor…"."""
    from PySide6.QtWidgets import QLabel

    console, toggle, _splitter = _catalog_console(shown_window)
    panel = shown_window.yulon_log_panels[0]
    said = "Step 7 of 9 · Compiling the world server"
    was_hidden = console.isHidden(), toggle.isHidden()
    try:
        console.setVisible(True)
        _at_width(shown_window, (1920, 1080))
        panel.append("Step 7 of 9 (77%): Compiling the world server")
        process_events(20)
        (label,) = [w for w in panel.findChildren(QLabel) if w.toolTip() == said]
        assert label.text() == said, label.text()
    finally:
        panel.clear()
        panel._clear_strip()
        console.setHidden(was_hidden[0])
        toggle.setHidden(was_hidden[1])
        process_events(10)
