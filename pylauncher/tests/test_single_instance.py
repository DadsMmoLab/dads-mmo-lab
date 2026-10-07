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
from typing import Any

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
from yulon.ui import message_box

message_box.show_warning = say_instead_of_a_box
message_box.show_information = say_instead_of_a_box


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
    if role == "slow":
        # A cold start: the lock is taken and the socket is listening, but the
        # event loop that answers it does not run until this returns.
        print("T152 building", flush=True)
        time.sleep(float(os.environ["YULON_T152_SLOW_S"]))
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

The second gives up when `CLAIM_WAIT_MS` runs out on a connected socket that
never answered; the test kills the hung one as soon as the second has spoken,
so this is a ceiling, not a cost.
"""

SLOW_START_SECONDS = 8.0
"""How long the slow first copy spends in `build_window()` before its event loop runs.

Past the five seconds round 1 gave a connected first copy to answer, and well
inside `CLAIM_WAIT_MS`, so the old code shows the box and the new one waits.
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
    # The children's own runtime dir, private as the spec says one is, so the
    # socket's place does not depend on whether this box's variable is real.
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700, exist_ok=True)
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
            "XDG_RUNTIME_DIR": str(runtime),
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
    if sys.platform.startswith("linux"):
        # Where the two meet: this user's runtime dir, not a guessable name in /tmp.
        assert list((tmp_path / "run").glob("yulon-*")), "the socket is not in XDG_RUNTIME_DIR"

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
    # No runtime dir, so the socket goes where Qt puts it: under the long TMPDIR.
    first, first_out = _start(
        tmp_path, launches, "first", "window", TMPDIR=str(deep), XDG_RUNTIME_DIR=""
    )
    _wait_for(first_out, first, "T152 ready")

    second = _run(
        tmp_path,
        "window",
        TMPDIR=str(deep),
        XDG_RUNTIME_DIR="",
        YULON_T152_LIFETIME_MS=SECOND_WINDOW_LIFETIME_MS,
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


@pytest.mark.slow
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


@pytest.mark.slow
def test_a_second_launch_during_the_first_ones_slow_start_waits_for_it_and_raises_it(
    tmp_path: Path, launches: list[subprocess.Popen[bytes]]
) -> None:
    """A first copy still in `build_window()` has the lock and a socket, and cannot answer yet.

    Round 1 gave it five seconds and then showed the "already open, may be
    stuck" box - after which the first window came forward anyway, answering a
    launch that had already given up (cold review, reproduced with a 15 s
    `build_window`; a frozen Windows build under a virus scanner is that).
    """
    first, first_out = _start(
        tmp_path, launches, "first", "slow", YULON_T152_SLOW_S=str(SLOW_START_SECONDS)
    )
    _wait_for(first_out, first, "T152 building")

    second = _run(tmp_path, "window", YULON_T152_LIFETIME_MS=SECOND_WINDOW_LIFETIME_MS)

    assert "T152 box" not in second.stdout, second.stdout
    assert "T152 ready" not in second.stdout, f"the second launch opened a window:\n{second.stdout}"
    assert second.returncode == 0, second.stdout
    assert first.wait(timeout=HANG_BOUND) == 0, _said(first_out)
    assert "T152 brought" in _said(first_out), _said(first_out)


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


def _held(lock_path: Path) -> bool:
    """Whether somebody holds `lock_path`, asked by trying it and letting go at once."""
    from PySide6.QtCore import QLockFile

    probe = QLockFile(str(lock_path))
    if probe.tryLock(0):
        probe.unlock()
        return False
    return True


@pytest.mark.parametrize(
    "case", ["a-launch-took-the-gap", "took-the-gap-while-busy", "nobody-came"]
)
def test_a_failed_docker_group_restart_closes_this_copy_if_another_took_the_lock(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """The exec gap, driven through the real view method and the real `restart_under_docker_group`.

    Two things round 1 got wrong (Codex, high). The argv was worked out INSIDE
    the gap - `docker_group_reexec()` can run `id -nG` for five seconds - so
    every call to it must find the lock still held. And when the exec fails
    after another launch has taken the lock, this copy carried on unguarded
    beside it; it must close. Only `os.execv` is replaced: it lets the
    competitor in (or not), then fails the way a missing `sg` does.

    Round 3 (Codex, high): the close must not wait on the player. Round 2 put a
    MODAL box up first and closed only once it was dismissed, so the losing
    copy and its jobs ran on beside the winner for as long as nobody pressed
    OK. The warning here stands in for a modal nobody answers: it never
    returns. When the close is refused (a database import running, which must
    not be force-quit), the explanation is a box that does not block.
    """
    from PySide6.QtCore import QEvent, QObject, Qt

    competitor = case != "nobody-came"

    class _NeverAnswered(Exception):
        pass

    class _RefuseClose(QObject):
        def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # noqa: N802
            if event.type() is QEvent.Type.Close:
                event.ignore()
                return True
            return False

    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QMainWindow, QMessageBox

    from yulon import platform
    from yulon.catalog.catalog import load_catalog
    from yulon.ui import single_instance
    from yulon.ui.catalog_view import CatalogView
    from yulon.ui.widgets.log_panel import LogPanel

    config = tmp_path / "config"
    lock_path = config / single_instance.LOCK_NAME
    guard = single_instance.InstanceGuard(config)
    rival = QLockFile(str(lock_path))
    asked_while_held: list[bool] = []
    exec_found_it_free: list[bool] = []

    def reexec() -> list[str]:
        asked_while_held.append(_held(lock_path))
        return ["/nonexistent/sg", "docker", "-c", "yulon"]

    def execv(_path: str, _argv: list[str]) -> None:
        exec_found_it_free.append(not _held(lock_path))
        if competitor:
            assert rival.tryLock(0), "the competitor could not take the free lock"
        raise OSError("sg: not found")

    monkeypatch.setattr(platform, "docker_group_reexec", reexec)
    monkeypatch.setattr(platform.os, "execv", execv)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    boxes: list[str] = []

    def warning(_parent: object, title: str, *_a: object, **_k: object) -> object:
        boxes.append(title)
        if competitor:
            raise _NeverAnswered(
                f"a modal {title!r} while the window was visible: {window.isVisible()}"
            )
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", warning)
    window = QMainWindow()
    refuse = _RefuseClose(window)
    view = CatalogView(load_catalog(), lambda e: None, LogPanel(), pick_dir=lambda *_: tmp_path)
    window.setCentralWidget(view)
    try:
        assert guard.claim() == "first"
        window.show()
        if case == "took-the-gap-while-busy":
            window.installEventFilter(refuse)

        assert view._offer_a_restart_instead("the install could not reach Docker") is True

        assert asked_while_held and all(asked_while_held), asked_while_held
        assert exec_found_it_free == [True], "the lock was not free for the exec"
        lost_boxes = [
            box
            for box in window.findChildren(QMessageBox)
            if box.isVisible() and box.windowTitle() == single_instance.LOST_TITLE
        ]
        if case == "a-launch-took-the-gap":
            assert boxes == [], boxes
            assert not window.isVisible(), "this copy kept running beside the one that won"
        elif case == "took-the-gap-while-busy":
            assert boxes == [], boxes
            assert window.isVisible(), "a refused close was forced"
            assert len(lost_boxes) == 1, "the refused close was not explained"
            assert lost_boxes[0].windowModality() is Qt.WindowModality.NonModal
        else:
            assert boxes == ["Install failed"], boxes
            assert window.isVisible()
            assert _held(lock_path), "the exec failed and the lock was not taken back"
    finally:
        window.removeEventFilter(refuse)
        window.close()
        guard.release()
        if rival.isLocked():
            rival.unlock()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG_RUNTIME_DIR is Linux's")
@pytest.mark.parametrize(
    "case",
    ["private", "unset", "missing", "shared-mode", "someone-elses", "too-long"],
)
def test_the_socket_goes_in_the_runtime_dir_only_when_it_is_this_users_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """A guessable name in `/tmp` can be created first by another local user (cold review).

    Every case but `private` breaks exactly one of the things that make a
    runtime dir private, and each must fall back to Qt's temp dir.
    """
    from yulon.ui import single_instance

    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    if case == "unset":
        monkeypatch.delenv("XDG_RUNTIME_DIR")
    elif case == "missing":
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "not-there"))
    elif case == "shared-mode":
        runtime.chmod(0o755)
    elif case == "someone-elses":
        someone_else = os.getuid() + 1
        monkeypatch.setattr(single_instance.os, "getuid", lambda: someone_else)
    elif case == "too-long":
        runtime = tmp_path / ("r" * 100)
        runtime.mkdir(mode=0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    name = single_instance.server_name_for(tmp_path / "config" / single_instance.LOCK_NAME)

    if case == "private":
        assert Path(name).parent == runtime, name
    else:
        assert not name.startswith(str(runtime)), name
        assert "/" not in name or name.startswith("/tmp/"), name


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


class _LineSocket:
    """What `InstanceGuard._read` uses of a `QLocalSocket`, holding one line."""

    def __init__(self, line: bytes) -> None:
        from PySide6.QtCore import QByteArray

        self._line = QByteArray(line)
        self.written: list[bytes] = []
        self.aborted = False

    def canReadLine(self) -> bool:  # noqa: N802
        return True

    def bytesAvailable(self) -> int:  # noqa: N802
        return len(self._line)

    def readLine(self, _max: int) -> Any:  # noqa: N802
        return self._line

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))

    def flush(self) -> None:
        return None

    def disconnectFromServer(self) -> None:  # noqa: N802
        return None

    def abort(self) -> None:
        self.aborted = True


def test_a_sign_in_start_asks_whether_yulon_is_there_without_raising_it(
    qapp: object, tmp_path: Path
) -> None:
    """T540, normal review [P2]: a `--tray` launch that found Yu'lon open brought its window
    forward. It now says "present", which is answered and raises nothing."""
    from yulon.ui import single_instance

    guard = single_instance.InstanceGuard(tmp_path / "config")
    raised: list[str] = []
    guard.raise_requested.connect(raised.append)
    present = _LineSocket(b"present\n")
    guard._read(present)  # type: ignore[arg-type]
    assert raised == [] and present.written == [b"ok\n"] and not present.aborted
    ask = _LineSocket(b"raise tok\n")
    guard._read(ask)  # type: ignore[arg-type]
    assert raised == ["tok"] and ask.written == [b"ok\n"]
