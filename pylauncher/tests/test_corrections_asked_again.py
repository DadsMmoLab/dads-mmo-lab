"""A corrections reading nobody could answer is asked again while the database stays up (T381).

Seen on yulon-ubuntu, 2026-10-05 (T302's live test): a Tortoise server
crash-looped on its weekly honour maintenance, because `tw_char` lacked
`character_inventory_copy`. The fix for that table was already shipped as a
database correction (T159), and the Server tab offers corrections on a banner --
but the tab asked once, 6 s after the restart's `compose up`, while MariaDB was
still starting (`ERROR 2002 ... Can't connect`). That reading is `unreadable`,
which shows no banner, and the tab never asked again: the question is put once
per database start, and in a crash loop only the world restarts. The banner was
missing exactly where it was needed.

Now an `unreadable` reading taken while the database is up is asked again on a
later poll, after a growing wait and a bounded number of times. Every other
answer is final until the database goes down and comes back, as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_plan_corrections import _database, _Route, _view, ps  # noqa: F401 - fixture
from yulon.catalog import native
from yulon.ui import controller_view as controller_view_module


class _Answers(_Route):
    """A route whose readings are taken in turn; the last one repeats."""

    def __init__(self, *states: str) -> None:
        super().__init__(states[-1])
        self.states = list(states)

    def check(self) -> native.CorrectionCheck:
        self.state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return super().check()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(controller_view_module, "_corrections_clock", fake)
    return fake


WAITS = controller_view_module.CORRECTIONS_ASKED_AGAIN_AFTER


def test_a_reading_taken_while_the_database_was_starting_is_asked_again(
    qapp: object, ps: object, tmp_path: Path, clock: _Clock
) -> None:
    route = _Answers("unreadable", "stale")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    assert route.checks == 1
    assert view.corrections_banner.isHidden()  # type: ignore[attr-defined]
    _database(ps, view, up=True)
    assert route.checks == 1, "not on every poll"
    clock.now += WAITS[0]
    _database(ps, view, up=True)
    assert route.checks == 2
    assert not view.corrections_banner.isHidden()  # type: ignore[attr-defined]
    clock.now += max(WAITS)
    _database(ps, view, up=True)
    assert route.checks == 2, "an answer is final until the database goes down"


def test_the_waits_grow_and_the_asking_ends(
    qapp: object, ps: object, tmp_path: Path, clock: _Clock
) -> None:
    route = _Answers("unreadable")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    for asked, wait in enumerate(WAITS, start=2):
        clock.now += wait - 1
        _database(ps, view, up=True)
        assert route.checks == asked - 1, f"asked before {wait} s had passed"
        clock.now += 1
        _database(ps, view, up=True)
        assert route.checks == asked
    clock.now += 10 * max(WAITS)
    _database(ps, view, up=True)
    assert route.checks == 1 + len(WAITS), "bounded"
    assert list(WAITS) == sorted(WAITS) and len(set(WAITS)) == len(WAITS)


def test_a_database_that_comes_back_is_asked_afresh(
    qapp: object, ps: object, tmp_path: Path, clock: _Clock
) -> None:
    route = _Answers("unreadable")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    for wait in WAITS:
        clock.now += wait
        _database(ps, view, up=True)
    _database(ps, view, up=False)
    route.states = ["unreadable", "stale"]
    _database(ps, view, up=True)
    clock.now += WAITS[0]
    _database(ps, view, up=True)
    assert not view.corrections_banner.isHidden()  # type: ignore[attr-defined]


def test_a_retry_never_runs_while_the_database_is_down(
    qapp: object, ps: object, tmp_path: Path, clock: _Clock
) -> None:
    route = _Answers("unreadable", "stale")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    _database(ps, view, up=False)
    clock.now += max(WAITS)
    _database(ps, view, up=False)
    assert route.checks == 1
