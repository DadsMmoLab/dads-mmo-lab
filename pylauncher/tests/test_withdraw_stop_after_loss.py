"""T607 item 6: a Stop taken back as "too late" is never a reservation lost from elsewhere.

`withdraw_stop()` clears a press's cancel when a Stop comes after the build already met its proof,
so the press SUCCEEDS (T247). T568 made a lost reservation set that same cancel, so the clear
also erased a loss: a rebuild whose server another Yu'lon had just stopped (Stop anyway) said
"The server is up." and succeeded. A loss is not withdrawn; the press ends as it does for a loss
earlier in the watch.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.conftest import HANG_BOUND
from tests.support_native import engine
from tests.test_ready_wait_stop import WATCH_PAUSES
from tests.test_update_to_latest import _ready
from yulon import docker
from yulon.after_stop import withdraw_stop
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions


def test_a_plain_stop_is_withdrawn() -> None:
    cancel = threading.Event()
    cancel.set()
    assert withdraw_stop(cancel) is True
    assert not cancel.is_set()


def test_a_cancel_set_by_a_lost_reservation_is_not_withdrawn() -> None:
    lost = threading.Event()
    cancel = native._end_on_loss(docker.ClaimHeld("yulon-busy-x", lost), None, threading.Event())
    lost.set()
    assert cancel.wait(HANG_BOUND)
    assert withdraw_stop(cancel) is False
    assert cancel.is_set(), "the loss was erased"


def test_a_press_cancel_whose_reservation_is_lost_is_not_withdrawn() -> None:
    lost = threading.Event()
    stop = threading.Event()
    cancel = native.PressCancel(stop, lost)
    lost.set()
    assert cancel.is_set()
    assert withdraw_stop(cancel) is False
    assert cancel.is_set()


def test_a_stop_after_a_loss_cannot_be_withdrawn_either() -> None:
    """Both set: the loss stands, so the press does not succeed."""
    lost = threading.Event()
    cancel = native._end_on_loss(docker.ClaimHeld("yulon-busy-x", lost), None, threading.Event())
    cancel.set()
    lost.set()
    assert withdraw_stop(cancel) is False
    assert cancel.is_set()


def test_withdrawing_nothing_is_a_no_op() -> None:
    assert withdraw_stop(None) is False


def test_a_rebuild_whose_reservation_is_lost_in_the_watchs_last_pause_does_not_succeed(
    qapp: object, tmp_path: Path
) -> None:
    rec, server_dir = _ready(tmp_path)
    lost = threading.Event()
    pauses = {"n": 0}

    @contextmanager
    def reservation(*_a: object, **_k: object) -> Iterator[docker.ClaimHeld]:
        yield docker.ClaimHeld("yulon-busy-x", lost)

    def pause(_seconds: float) -> None:
        rec.calls.append("pause")
        pauses["n"] += 1
        if pauses["n"] == WATCH_PAUSES:
            lost.set()  # another Yu'lon's "Stop anyway", in the watch's last pause
            time.sleep(0.6)  # the loss watcher polls every 0.2 s and sets the press's own cancel

    made = engine(rec, sleep=pause, server_claim=reservation)
    said: list[str] = []
    with pytest.raises(InstallerError) as ended:
        for line in made.rebuild(InstallOptions(server_dir=server_dir)):
            said.append(line)
    text = "\n".join(said)
    assert native.READY_STOP_TOO_LATE not in text, text
    assert "was rebuilt and is running" not in text, "the press reported success"
    assert native.READY_LOST_IN_THE_WATCH in str(ended.value) + text, text
    assert "ended from elsewhere" in text, text
