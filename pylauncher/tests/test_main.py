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

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import main
from tests.conftest import process_events
from yulon import platform, state, update


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
    step = r"downloaded the installer → C:\Users\pk\x.exe"
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
    """
    from PySide6.QtWidgets import QApplication

    from yulon.ui.controller_view import ControllerView

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(update, "check_for_update", lambda: None)
    monkeypatch.setattr(state, "load_state", lambda path=None: state.AppState(installs=[]))
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

    window = main.build_window()
    window.saved_states = saved
    yield window

    # `_stop_background_threads` itself, not a hand-rolled equivalent: a QThread
    # destroyed while running ABORTS the process, and a leaked one takes the
    # whole run down with it.
    main._stop_background_threads(window)
    QApplication.processEvents()
    monkeypatch.undo()


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
