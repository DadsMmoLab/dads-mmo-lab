"""The repair-import and adopt readings are asked again, like the corrections check (T420).

`_ask_about_the_import()` puts three questions once each time the database comes
up. T381 gave the corrections check a bounded ask-again after an `unreadable`
answer; the import probe (the Repair button) and the adopt reading did not get
one, so a reading taken a few seconds after `compose up` hid its button until
the database went down and came back -- which a world crash loop never does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import _Ps
from tests.test_corrections_asked_again import _Clock, clock, ps  # noqa: F401
from tests.test_plan_corrections import _database, _Route, _view
from yulon import docker
from yulon.ui import controller_view as controller_view_module

WAITS = controller_view_module.CORRECTIONS_ASKED_AGAIN_AFTER


class _Reads:
    """A probe whose readings come in turn; the last repeats."""

    def __init__(self, *states: str) -> None:
        self.states = list(states)
        self.asked = 0

    def __call__(self) -> docker.ImportState:
        self.asked += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return docker.ImportState(state, "a schema is empty")  # type: ignore[arg-type]

    def state(self) -> docker.ImportState:
        return self()


def _with(ps: _Ps, tmp_path: Path, *, imports: _Reads, adopts: _Reads) -> object:
    view = _view(ps, tmp_path, _Route("current"))
    view.services.controller.import_probe = imports  # type: ignore[attr-defined]
    view.services.adopt = adopts  # type: ignore[attr-defined]
    return view


def test_the_import_probe_is_asked_again_after_an_unreadable_answer(
    qapp: object, ps: _Ps, tmp_path: Path, clock: _Clock  # noqa: F811
) -> None:
    imports, adopts = _Reads("unreadable", "absent"), _Reads("imported")
    view = _with(ps, tmp_path, imports=imports, adopts=adopts)
    _database(ps, view, up=True)
    assert imports.asked == 1
    _database(ps, view, up=True)
    assert imports.asked == 1, "not on every poll"
    clock.now += WAITS[0]
    _database(ps, view, up=True)
    assert imports.asked == 2
    assert not view.repair_button.isHidden()  # type: ignore[attr-defined]
    clock.now += max(WAITS)
    _database(ps, view, up=True)
    assert imports.asked == 2, "an answer is final until the database goes down"
    assert adopts.asked == 1, "an answered reading is not asked again"


def test_the_adopt_reading_is_asked_again_after_an_unreadable_answer(
    qapp: object, ps: _Ps, tmp_path: Path, clock: _Clock  # noqa: F811
) -> None:
    imports, adopts = _Reads("imported"), _Reads("unreadable", "imported")
    view = _with(ps, tmp_path, imports=imports, adopts=adopts)
    _database(ps, view, up=True)
    clock.now += WAITS[0] - 1
    _database(ps, view, up=True)
    assert adopts.asked == 1
    clock.now += 1
    _database(ps, view, up=True)
    assert adopts.asked == 2
    assert imports.asked == 1


@pytest.mark.parametrize("which", ["import", "adopt"])
def test_the_waits_grow_the_asking_ends_and_a_restart_starts_afresh(
    qapp: object, ps: _Ps, tmp_path: Path, clock: _Clock, which: str  # noqa: F811
) -> None:
    imports, adopts = _Reads("imported"), _Reads("imported")
    reads = imports if which == "import" else adopts
    reads.states = ["unreadable"]
    view = _with(ps, tmp_path, imports=imports, adopts=adopts)
    _database(ps, view, up=True)
    for wait in WAITS:
        clock.now += wait
        _database(ps, view, up=True)
    assert reads.asked == 1 + len(WAITS)
    clock.now += 10 * max(WAITS)
    _database(ps, view, up=True)
    assert reads.asked == 1 + len(WAITS), "bounded"
    _database(ps, view, up=False)
    _database(ps, view, up=True)
    assert reads.asked == 2 + len(WAITS), "the database came back: asked afresh"
    clock.now += WAITS[0]
    _database(ps, view, up=True)
    assert reads.asked == 3 + len(WAITS), "and the waits start from the first again"
