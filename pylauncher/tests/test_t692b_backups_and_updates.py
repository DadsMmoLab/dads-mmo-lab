"""T692b: the sub-tab is called "Backups & updates" everywhere it is named (RED).

T692a moved the server presses onto it, so the tab mostly holds backups and
updates now and its title says so. The title lives in one constant,
`server_build_presses.MAINTENANCE_TAB`, and every player sentence that sends
someone to the tab is built from it rather than typed by hand. The scan reads
the same shown strings `test_server_build_presses` reads: whole string
expressions, never a docstring, and an f-string's fields drawn as `{…}`. A
docstring or a comment may keep saying "Maintenance": the widget, the icon and
the code are still named that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import pump_until_every_job_has_answered
from tests.test_controller_view import (
    WOTLK,
    _at,
    _controller_in_the_real_window,
    _Ps,
    _services,
    _tab_index,
)
from tests.test_server_build_presses import PYLAUNCHER, _shown_strings, _sources
from yulon import runner
from yulon.server_build_presses import MAINTENANCE_TAB, SERVER_BUILD_PLACE
from yulon.ui import controller_view as cv


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def test_the_tab_is_called_backups_and_updates_everywhere(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """One constant names the tab, and no player string spells the old title.

    Mutation: type "Maintenance tab" into one sentence, or put the title into
    `_add_panel_tab` as a literal, and this fails naming the line or the tab.
    """
    assert MAINTENANCE_TAB == "Backups & updates"
    typed = [
        f"{path.relative_to(PYLAUNCHER)}:{line}: spells the tab by hand: {shown!r}"
        for path in _sources()
        for line, shown, _joined in _shown_strings(path)
        if "Maintenance" in shown
    ]
    assert typed == [], "\n".join(typed)
    view = cv.ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    pump_until_every_job_has_answered("the tab's title")
    index = next(
        i
        for i in range(view._tabs.count())
        if view._tabs.widget(i) is not None
        and view._tabs.widget(i).isAncestorOf(view.server_version_section)
    )
    assert view._tabs.tabText(index) == cv._drawn_title(MAINTENANCE_TAB), (
        f"the Server version section's tab is {view._tabs.tabText(index)!r}, " "not MAINTENANCE_TAB"
    )
    assert SERVER_BUILD_PLACE == "“Server version” on the Backups & updates tab", SERVER_BUILD_PLACE


def test_the_renamed_tab_is_drawn_whole_at_960x640(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The longer title still fits its tab at the app's smallest window.

    "Backups & updates" is six characters longer than "Maintenance"; a bar too
    narrow for it scrolls the tab out of reach or cuts its label. The page
    under it is T692a's own fit test.
    """
    view = cv.ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, MAINTENANCE_TAB)
    _at(window, (960, 640))
    bar = view._tabs.tabBar()
    rect = bar.tabRect(_tab_index(view, MAINTENANCE_TAB))
    assert rect.width() > 0, "the renamed tab has no width at 960x640"
    assert rect.right() <= bar.rect().right(), "the tab bar scrolled the renamed tab out of reach"
    needed = bar.fontMetrics().horizontalAdvance(MAINTENANCE_TAB)
    assert needed <= rect.width(), f"the renamed title needs {needed}px in a {rect.width()}px tab"
