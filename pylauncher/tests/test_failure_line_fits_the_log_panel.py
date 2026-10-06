"""A long failure in the rebuild log panel is shown whole, at every window size (T450).

From the T382 live test on m910q (2026-10-06, `live-t382-m910q-2026-10-06`, step 6,
`t382-s6-rebuild-failed.png`): a Rebuild whose rollback did not come up either
ended on a failure of ten-odd wrapped lines, and in a maximised 1850x1016 window
the panel's failure label was given 119 px of the 153 it needed. The text stopped
at the world's last log line, and the two sentences after it -- T391's "Its
containers are still up … Press Stop on the Server tab to stop them." among them
-- were not on the screen at all.

A wrapped `QLabel`'s minimum is its height at its NARROWEST, and the panel's cap
(`_IdleLogPanel._share_of_the_tab()`) was floored at that minimum, so a failure
that wraps to more lines than the narrow guess was clipped by the cap.

Real widgets, themed at each width the way the app is
(`test_controller_view._at()`), and the real failure text: the one the rebuild
engine raises when both builds crash-loop.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import process_events, pump_until
from tests.support_native import Recorder, engine
from tests.test_controller_view import (
    WOTLK,
    _at,
    _controller_in_the_real_window,
    _layout_of,
    _Ps,
    _services,
)
from tests.test_rebuild import a_finished_install
from yulon import runner
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import ThreadedJobRunner

LIVE_LOG = "\n".join(
    [
        "Starting worldserver...",
        "> Config::LoadFile: Failed open file '/azerothcore/env/dist/etc/worldserver.conf'",
    ]
    * 3
)
"""What ac-worldserver printed on every run of the T391 sitting (chmod 000 on its conf)."""


def _the_real_failure(tmp_path: Path) -> InstallerError:
    """The exception a Rebuild raises when the new build and the rollback both crash-loop."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path / "engine")
    rec.ready = False
    rec.world_output = native.WorldOutput(LIVE_LOG, 6, "restarting")
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert native.ROLLBACK_LEFT_RUNNING in str(raised.value)
    return raised.value


def _settle(window: object) -> None:
    """The layout unchanged over three pumps in a row, as `_at()` waits, with no resize."""
    seen: list[object] = [None]
    same = [0]

    def settled() -> bool:
        process_events(10)
        now = _layout_of(window)
        same[0] = same[0] + 1 if now == seen[0] else 0
        seen[0] = now
        return same[0] >= 3

    pump_until(settled, "the window to settle after the failure")


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.mark.parametrize("size", [(800, 600), (1280, 800), (1850, 1016)])
def test_a_rollback_failure_is_shown_whole_in_the_rebuild_log(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: tuple[int, int],
) -> None:
    """The failure label is drawn at least as tall as its text needs at its width.

    Mutation: floor `_IdleLogPanel`'s cap at `minimumSizeHint()` alone again, and
    the label is drawn shorter than its wrapped text.
    """
    failure = _the_real_failure(tmp_path)
    runners: list[ThreadedJobRunner] = []

    def real_runner(parent: object) -> ThreadedJobRunner:
        runners.append(ThreadedJobRunner(parent))  # type: ignore[arg-type]
        return runners[-1]

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", real_runner)
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *_a, **_k: True)

    def rebuild(cancel: object = None) -> Iterator[str]:
        yield "The world server reported ready…"
        raise failure

    services = _services(ps, tmp_path, [])
    services.rebuild = rebuild
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    window, _body = _controller_in_the_real_window(view, "Modules")
    try:
        _at(window, size)
        assert view.rebuild_server() is True
        label = view.rebuild_log.failure_label
        pump_until(label.isVisible, "the rebuild's failure to reach the panel")
        # Settled WITHOUT a resize or a restyle: in the app nothing moves the
        # window after a failure lands, so the fit must not wait for one.
        _settle(window)

        assert "Press Stop on the Server tab" in label.text()
        needed = label.heightForWidth(label.width())
        assert label.height() >= needed, (
            f"at {size} the failure label is drawn {label.height()} px of the {needed} "
            "its text needs: the end of the failure is cut off"
        )
    finally:
        view.shutdown()
        window.close()
