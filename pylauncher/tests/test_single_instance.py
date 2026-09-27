"""T152: one Yu'lon per user. A second launch brings the first one's window forward.

Two copies of the app raced on the same server's state (the command-channel
password, tuning backups, install records), and nothing stopped a second one.

Most of this is proved with REAL processes, because the thing under test is two
processes finding each other: a lock file one of them holds and a local socket
the other one talks to. Each child runs the real `main.main()` against a
scratch config dir, offscreen; only the window it builds is a plain one (the
real window would ask GitHub for an update), except in the docker-group restart
test, which needs the real catalog view and stubs the update check instead, the
way `test_main.py`'s T113 children do.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import main
from tests.conftest import HANG_BOUND, pump_until

_LAUNCH = """\
import os, sys, time

sys.argv = ["yulon"]
from yulon import platform, update

# The real window asks GitHub for an update and writes `update.json`.
update.check_with_cache = lambda *a, **k: None

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox

import main

role = os.environ["YULON_T152_ROLE"]
main._regain_docker_group = lambda: None


def say_instead_of_a_box(_parent, title, text, *a, **k):
    # An offscreen modal waits for a click that never comes.
    print(f"T152 box {title} | {text}", flush=True)
    return QMessageBox.StandardButton.Ok


QMessageBox.warning = say_instead_of_a_box
QMessageBox.information = say_instead_of_a_box


class Window(QMainWindow):
    def raise_(self):
        print("T152 raise_", flush=True)
        super().raise_()

    def activateWindow(self):
        print("T152 activateWindow", flush=True)
        super().activateWindow()


real_front = getattr(main, "_bring_to_front", None)


def bring_to_front(window, token=""):
    real_front(window, token)
    print(f"T152 brought token={token!r} minimized={window.isMinimized()}", flush=True)
    QTimer.singleShot(0, window.close)


if real_front is not None:
    main._bring_to_front = bring_to_front

real_build_window = main.build_window


def restart_under_the_docker_group(window):
    from yulon.ui.answers import said_yes  # noqa: F401 - the real view imports it too
    from yulon.ui.catalog_view import CatalogView

    view = window.findChild(CatalogView)
    here = [sys.executable, "-c", os.environ["YULON_T152_SCRIPT"]]
    # What the exec'd process is told to be: an ordinary Yu'lon that closes soon.
    os.environ["YULON_T152_ROLE"] = "window"
    os.environ["YULON_T152_LIFETIME_MS"] = "500"
    platform.docker_group_reexec = lambda *a, **k: here
    QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes
    print(f"T152 exec from {os.getpid()}", flush=True)
    view._offer_a_restart_instead("the install could not reach Docker")
    print("T152 exec did not happen", flush=True)


def build_window():
    if role == "exec":
        window = real_build_window()
        QTimer.singleShot(0, lambda: restart_under_the_docker_group(window))
        return window
    window = Window()

    def ready():
        if os.environ.get("YULON_T152_MINIMIZE"):
            window.showMinimized()
        print(f"T152 ready {os.getpid()}", flush=True)
        if role == "hang":
            print("T152 hanging", flush=True)
            time.sleep(float(os.environ["YULON_T152_HANG_S"]))

    QTimer.singleShot(0, ready)
    QTimer.singleShot(int(os.environ.get("YULON_T152_LIFETIME_MS", "30000")), window.close)
    return window


main.build_window = build_window
code = main.main()
print(f"T152 main returned {code}", flush=True)
raise SystemExit(code)
"""

HUNG_FIRST_SECONDS = 40.0
"""How long the hung first copy blocks its event loop: well past the second's whole wait.

The second gives up after `REPLY_MS` of silence on a connected socket; the test
kills the hung one as soon as the second has spoken, so this is a ceiling, not
a cost.
"""

SECOND_WINDOW_LIFETIME_MS = "3000"
"""How long a second launch that wrongly opened a window keeps it before closing.

Only reached on a red run, where it stops the child from running until the
test's bound: the failure is then "it opened a window", not a timeout.
"""


@pytest.fixture
def launches(tmp_path: Path) -> Iterator[list[subprocess.Popen[bytes]]]:
    """Every long-lived child a test starts, killed at the end whatever happened."""
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=HANG_BOUND)


def _env(tmp_path: Path, role: str, **extra: str) -> dict[str, str]:
    """A child's environment: its own config dir and temp dir, offscreen, one role."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    scratch_temp = tmp_path / "temp"
    scratch_temp.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),  # macOS: config_dir() is under HOME and nothing else
            "APPDATA": str(home),
            "XDG_DATA_HOME": str(home),
            "QT_QPA_PLATFORM": "offscreen",
            "TMPDIR": str(scratch_temp),
            "TEMP": str(scratch_temp),
            "TMP": str(scratch_temp),
            "YULON_T152_ROLE": role,
            "YULON_T152_SCRIPT": _LAUNCH,
            "PYTHONPATH": str(Path(main.__file__).parent),
        }
    )
    for name in ("YULON_SMOKE_TEST", "YULON_PROVISION", "XDG_ACTIVATION_TOKEN"):
        env.pop(name, None)
    env.update(extra)
    return env


def _start(
    tmp_path: Path, started: list[subprocess.Popen[bytes]], name: str, role: str, **extra: str
) -> tuple[subprocess.Popen[bytes], Path]:
    """Start a Yu'lon that keeps running; its output goes to a file the test can poll."""
    out = tmp_path / f"{name}.out"
    with out.open("wb") as sink:
        process = subprocess.Popen(
            [sys.executable, "-c", _LAUNCH],
            cwd=Path(main.__file__).parent,
            env=_env(tmp_path, role, **extra),
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    started.append(process)
    return process, out


def _run(tmp_path: Path, role: str, **extra: str) -> subprocess.CompletedProcess[str]:
    """Run a Yu'lon to its end: the second launch, which should not stay."""
    return subprocess.run(
        [sys.executable, "-c", _LAUNCH],
        cwd=Path(main.__file__).parent,
        env=_env(tmp_path, role, **extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=HANG_BOUND,
    )


def _said(out: Path) -> str:
    return out.read_text(encoding="utf-8", errors="replace")


def _wait_for(out: Path, process: subprocess.Popen[bytes], text: str) -> None:
    """Until `text` is in the child's output; fail saying so if it ended or never said it."""
    deadline = time.monotonic() + HANG_BOUND
    while text not in _said(out):
        if process.poll() is not None:
            pytest.fail(f"the child ended ({process.returncode}) before {text!r}:\n{_said(out)}")
        if time.monotonic() > deadline:
            pytest.fail(f"no {text!r} within {HANG_BOUND}s:\n{_said(out)}")
        time.sleep(0.05)


def _the_lock(tmp_path: Path) -> list[Path]:
    """Every lock file under the children's config dir: the only lock this guard writes."""
    return sorted((tmp_path / "home").rglob("yulon.lock"))


# ------------------------------------------------------------ two real processes


def test_a_second_launch_brings_the_first_window_forward_and_exits_0(
    tmp_path: Path, launches: list[subprocess.Popen[bytes]]
) -> None:
    """The owner's decision, end to end: the second copy never opens, the first comes forward.

    The first is MINIMIZED before the second starts, because "bring to the
    front" that leaves a minimized window minimized is the failure a player
    would see. The second carries an activation token, which is what a Wayland
    desktop hands the process it launched; only the first has a window to spend
    it on, so it must travel.
    """
    first, first_out = _start(tmp_path, launches, "first", "window", YULON_T152_MINIMIZE="1")
    _wait_for(first_out, first, "T152 ready")

    second = _run(
        tmp_path,
        "window",
        YULON_T152_LIFETIME_MS=SECOND_WINDOW_LIFETIME_MS,
        XDG_ACTIVATION_TOKEN="t152-token",
    )

    assert "T152 ready" not in second.stdout, f"the second launch opened a window:\n{second.stdout}"
    assert "T152 box" not in second.stdout, second.stdout
    assert second.returncode == 0, second.stdout
    assert first.wait(timeout=HANG_BOUND) == 0, _said(first_out)
    said = _said(first_out)
    assert "T152 brought token='t152-token' minimized=False" in said, said
    assert "T152 raise_" in said and "T152 activateWindow" in said, said


@pytest.mark.skipif(
    sys.platform == "win32", reason="a pipe name is not a path; no length to exceed"
)
def test_a_temp_dir_too_long_for_a_socket_path_still_lets_the_second_launch_through(
    tmp_path: Path, launches: list[subprocess.Popen[bytes]]
) -> None:
    """Qt puts the socket file in the temp dir, and a socket path has a hard limit.

    Measured with PySide6 6.11.2 on Linux: under a temp dir whose socket path is
    past `sun_path`'s 108 bytes, `listen()` fails with "Name error". The first
    copy then holds the lock with nobody listening, and every later launch waits
    out the whole claim and reports it stuck.
    """
    deep = tmp_path / "temp" / ("t" * 120)
    deep.mkdir(parents=True)
    first, first_out = _start(tmp_path, launches, "first", "window", TMPDIR=str(deep))
    _wait_for(first_out, first, "T152 ready")

    second = _run(
        tmp_path, "window", TMPDIR=str(deep), YULON_T152_LIFETIME_MS=SECOND_WINDOW_LIFETIME_MS
    )

    assert "T152 ready" not in second.stdout, f"the second launch opened a window:\n{second.stdout}"
    assert second.returncode == 0, second.stdout
    assert first.wait(timeout=HANG_BOUND) == 0, _said(first_out)
    assert "T152 brought" in _said(first_out), _said(first_out)


def test_a_lock_left_by_a_killed_yulon_does_not_stop_the_next_launch(
    tmp_path: Path, launches: list[subprocess.Popen[bytes]]
) -> None:
    """A crash leaves the lock file (and, on Linux and macOS, the socket file) behind.

    The relaunch must take both over: the lock, or it would refuse to open at
    all, and the socket name, or it would open but no LATER launch could ever
    reach it. So the third launch is the half that proves the socket.
    """
    first, first_out = _start(tmp_path, launches, "first", "window")
    _wait_for(first_out, first, "T152 ready")
    first.kill()
    first.wait(timeout=HANG_BOUND)
    assert _the_lock(tmp_path), "the kill left no lock behind, so nothing here is stale"

    relaunched, relaunched_out = _start(tmp_path, launches, "relaunched", "window")
    _wait_for(relaunched_out, relaunched, "T152 ready")
    assert "T152 box" not in _said(relaunched_out), _said(relaunched_out)

    third = _run(tmp_path, "window", YULON_T152_LIFETIME_MS=SECOND_WINDOW_LIFETIME_MS)

    assert "T152 ready" not in third.stdout, f"the third launch opened a window:\n{third.stdout}"
    assert third.returncode == 0, third.stdout
    assert relaunched.wait(timeout=HANG_BOUND) == 0, _said(relaunched_out)
    assert "T152 brought" in _said(relaunched_out), _said(relaunched_out)


def test_a_first_copy_that_does_not_answer_is_named_in_a_box_not_waited_on(
    tmp_path: Path, launches: list[subprocess.Popen[bytes]]
) -> None:
    """A first copy whose window is stuck holds the lock and never replies.

    The second must neither open a window of its own beside it (the race this
    ticket exists for) nor sit there forever with nothing on screen.
    """
    first, first_out = _start(
        tmp_path, launches, "first", "hang", YULON_T152_HANG_S=str(HUNG_FIRST_SECONDS)
    )
    _wait_for(first_out, first, "T152 hanging")

    asked = time.monotonic()
    second = _run(tmp_path, "window", YULON_T152_LIFETIME_MS=SECOND_WINDOW_LIFETIME_MS)
    took = time.monotonic() - asked

    assert "T152 ready" not in second.stdout, f"the second launch opened a window:\n{second.stdout}"
    assert "T152 box Yu'lon is already open" in second.stdout, second.stdout
    assert second.returncode == 1, second.stdout
    # Called stuck on its silence, not merely waited out: a copy that answers
    # nothing on a live connection is not one that is still starting.
    assert "accepted but did not answer" in second.stdout, second.stdout
    assert took < HUNG_FIRST_SECONDS, f"the second waited out the whole hang ({took:.1f}s)"


def test_closing_yulon_lets_the_next_launch_open(
    tmp_path: Path, launches: list[subprocess.Popen[bytes]]
) -> None:
    """The self-update's relaunch, and any reopen: a closed Yu'lon leaves nothing in the way.

    The update helper waits for the old process to END and then starts the new
    build (`selfupdate/swap.py`), so the new one only needs the old one's close
    to have given the lock back - and a normal close removes the file rather
    than leaving it to the dead-process check.
    """
    first = _run(tmp_path, "window", YULON_T152_LIFETIME_MS="500")
    assert first.returncode == 0 and "T152 ready" in first.stdout, first.stdout
    assert not _the_lock(tmp_path), f"a normal close left {_the_lock(tmp_path)}"

    after, after_out = _start(tmp_path, launches, "after", "window", YULON_T152_LIFETIME_MS="500")
    assert after.wait(timeout=HANG_BOUND) == 0, _said(after_out)
    assert "T152 ready" in _said(after_out), _said(after_out)


@pytest.mark.skipif(sys.platform == "win32", reason="`os.execv` and `sg` are Linux-only paths")
def test_the_docker_group_restart_hands_the_lock_to_the_process_it_becomes(
    tmp_path: Path, launches: list[subprocess.Popen[bytes]]
) -> None:
    """`CatalogView._offer_a_restart_instead()` replaces the running app with `os.execv`.

    Same PID, new program: the lock file names a process that is still running
    (it is this one), so without a handover the restarted app finds its own
    lock, asks a socket nobody listens on any more, and tells the player that
    Yu'lon is already open. Driven through the REAL view method on the real
    window; only the argv it execs and the Yes it is given are supplied.
    """
    process, out = _start(tmp_path, launches, "exec", "exec")

    assert process.wait(timeout=HANG_BOUND) == 0, _said(out)
    said = _said(out)
    assert "T152 exec from" in said and "T152 exec did not happen" not in said, said
    assert "T152 box" not in said, said
    before = said.split("T152 exec from ", 1)[1].split()[0]
    assert f"T152 ready {before}" in said, f"the restarted app did not open:\n{said}"


# ------------------------------------------------------------ in-process halves


def test_the_first_copy_answers_a_raise_and_refuses_anything_else(
    qapp: object, tmp_path: Path
) -> None:
    """The server half, spoken to by a raw socket: the line protocol and nothing else."""
    from PySide6.QtNetwork import QLocalSocket

    from yulon.ui.single_instance import InstanceGuard

    guard = InstanceGuard(tmp_path / "config")
    try:
        assert guard.claim() == "first"
        asked: list[str] = []
        guard.raise_requested.connect(asked.append)

        client = QLocalSocket()
        client.connectToServer(guard.server_name)
        pump_until(
            lambda: client.state() == QLocalSocket.LocalSocketState.ConnectedState, "connect"
        )
        client.write(b"raise some-token\n")
        pump_until(client.canReadLine, "the reply")
        assert bytes(client.readLine().data()) == b"ok\n"
        assert asked == ["some-token"]

        stranger = QLocalSocket()
        stranger.connectToServer(guard.server_name)
        pump_until(
            lambda: stranger.state() == QLocalSocket.LocalSocketState.ConnectedState, "connect"
        )
        stranger.write(b"format c:\n")
        pump_until(
            lambda: stranger.state() == QLocalSocket.LocalSocketState.UnconnectedState,
            "the stranger to be dropped",
        )
        assert asked == ["some-token"], asked
    finally:
        guard.release()


@pytest.mark.parametrize("blocked_by", ["a file where the directory goes", "a read-only directory"])
def test_a_config_dir_that_cannot_hold_a_lock_runs_unguarded_at_once(
    qapp: object, tmp_path: Path, blocked_by: str
) -> None:
    """A lock that cannot be WRITTEN is not a lock someone else holds.

    Treating it as held would wait out the whole claim and then tell the player
    another Yu'lon is open when none is. Two ways in, because they fail in two
    places: the directory cannot be made, or it exists and the lock file cannot
    be created in it. `test_main.py`'s unwritable-config-dir entry-point test is
    the first of them through `main()`.
    """
    from yulon.ui.single_instance import CLAIM_WAIT_MS, InstanceGuard

    if blocked_by == "a file where the directory goes":
        blocker = tmp_path / "a-file"
        blocker.write_text("not a directory", encoding="utf-8")
        directory = blocker / "yulon"
    else:
        if sys.platform == "win32" or os.geteuid() == 0:
            pytest.skip("a mode bit stops neither Windows' ACLs nor root")
        directory = tmp_path / "yulon"
        directory.mkdir()
        directory.chmod(0o500)
    try:
        asked = time.monotonic()
        assert InstanceGuard(directory).claim() == "unguarded"
        assert time.monotonic() - asked < CLAIM_WAIT_MS / 1000, "it waited out the whole claim"
    finally:
        if directory.is_dir():
            directory.chmod(0o700)


def test_handing_over_gives_the_lock_up_and_takes_it_back_when_the_exec_fails(
    qapp: object, tmp_path: Path
) -> None:
    """`handed_over()`'s far side is only reached when `os.execv` did NOT replace us."""
    from PySide6.QtCore import QLockFile

    from yulon.ui import single_instance

    guard = single_instance.InstanceGuard(tmp_path / "config")
    try:
        assert guard.claim() == "first"
        probe = QLockFile(str(tmp_path / "config" / single_instance.LOCK_NAME))
        assert not probe.tryLock(0), "the guard does not hold its own lock"
        with single_instance.handed_over():
            assert probe.tryLock(0), "the lock was not given up for the exec"
            probe.unlock()
        assert not probe.tryLock(0), "the exec failed and the lock was not taken back"
    finally:
        guard.release()


def test_bringing_forward_restores_a_minimized_window_and_keeps_it_maximized(
    qapp: object,
) -> None:
    """A maximized window that was minimized comes back maximized, not at its old size."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMainWindow

    window = QMainWindow()
    try:
        window.showMaximized()
        window.setWindowState(window.windowState() | Qt.WindowState.WindowMinimized)
        assert window.isMinimized()

        main._bring_to_front(window, "")

        assert not window.isMinimized()
        assert window.isMaximized()
    finally:
        window.close()


@pytest.mark.parametrize("platform_name", ["wayland", "xcb"])
def test_a_token_reaches_the_activation_on_wayland_and_is_never_left_behind(
    qapp: object, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """Qt's Wayland plugin reads the token from the environment when the window activates.

    So it must be there DURING `activateWindow()` on Wayland, and gone after on
    every platform: unspent (off Wayland, or refused), it would reach every
    process the app starts - a game client included - as if it were theirs.
    """
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QMainWindow

    seen: list[str | None] = []

    class Window(QMainWindow):
        def activateWindow(self) -> None:  # noqa: N802 - Qt's name
            seen.append(os.environ.get("XDG_ACTIVATION_TOKEN"))
            super().activateWindow()

    monkeypatch.delenv("XDG_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(QGuiApplication, "platformName", staticmethod(lambda: platform_name))
    window = Window()
    try:
        window.show()
        main._bring_to_front(window, "t152-token")
        assert seen == (["t152-token"] if platform_name == "wayland" else [None])
        assert "XDG_ACTIVATION_TOKEN" not in os.environ
    finally:
        window.close()
