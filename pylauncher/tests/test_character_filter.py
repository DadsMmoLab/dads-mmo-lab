"""T806: the filter box above the Characters and Bots lists.

The player's ask (Discord, 2026-10-18): "can we add a search bar to find bot names or
characters?" The Characters roster narrows locally, live as typed; the Bots browse
re-reads its first page a beat after typing stops, its filter now a substring. Both are
emptied by the line edit's built-in X, and neither press costs the choice its validity —
nor, since T221, a typed letter the sub-tab bar's way.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QToolButton  # noqa: E402

from tests.conftest import pump_until  # noqa: E402
from tests.test_characters_tab import _Play, _view  # noqa: E402
from yulon.catalog.catalog import load_catalog  # noqa: E402
from yulon.ui.widgets.job import run_inline  # noqa: E402

WOTLK = load_catalog().get("wow-wotlk")


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the view's background jobs synchronously, as every view test here does."""
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _roster() -> tuple:
    return (
        (1, "Guglu", 78, True, "ADMIN"),
        (2, "Ganaar", 7, False, "PLAYER"),
        (3, "Rexxar", 55, True, "PLAYER"),
        (4, "Lyra", 12, False, "ALLY"),
    )


def _roster_view(tmp_path: Path) -> object:
    from yulon.play import Character

    view = _view(tmp_path, play=_Play(characters=tuple(Character(*row) for row in _roster())))
    view.refresh_characters()
    return view


def _shown(view: object) -> list[str]:
    return [
        item.data(Qt.ItemDataRole.UserRole)
        for row in range(view.character_list.count())
        if not (item := view.character_list.item(row)).isHidden()
    ]


# -- the Characters roster ----------------------------------------------------


def test_typing_narrows_the_roster_to_names_containing_the_text(
    qapp: object, tmp_path: Path
) -> None:
    """Case-insensitive substring: "Gug" finds Guglu; "gug" does too."""
    view = _roster_view(tmp_path)
    assert len(_shown(view)) == 4

    view.character_filter.setText("Gug")
    assert _shown(view) == ["Guglu"]

    view.character_filter.setText("gug")
    assert _shown(view) == ["Guglu"], "the match is case-insensitive"

    view.character_filter.setText("aar")
    assert _shown(view) == ["Ganaar"], "a middle-of-name hit counts too"


def test_the_filter_matches_the_name_not_the_row_text(qapp: object, tmp_path: Path) -> None:
    """A row's text carries level, status, account and GUID; the search asks for names."""
    view = _roster_view(tmp_path)

    view.character_filter.setText("player")
    assert _shown(view) == [], "'PLAYER' is on the row text and in no name"

    view.character_filter.setText("level")
    assert _shown(view) == []


def test_the_clear_button_empties_the_filter_and_restores_the_rows(
    qapp: object, tmp_path: Path
) -> None:
    view = _roster_view(tmp_path)
    view.character_filter.setText("gug")
    assert _shown(view) == ["Guglu"]

    button = view.character_filter.findChild(QToolButton)
    assert button is not None, "the line edit has no clear button to click"
    button.click()

    assert view.character_filter.text() == ""
    assert len(_shown(view)) == 4


def test_a_choice_the_filter_hides_is_no_longer_the_choice(qapp: object, tmp_path: Path) -> None:
    """The actions grey out rather than act on a character the player cannot see."""
    view = _roster_view(tmp_path)
    view.character_list.setCurrentRow(1)  # Ganaar
    assert view._chosen_character() == "Ganaar"

    view.character_filter.setText("gug")

    assert _shown(view) == ["Guglu"]
    assert view._chosen_character() == "", "the hidden row is not the choice any more"
    for button in view.character_buttons():
        assert button.isEnabled() is False, button.text()


def test_a_choice_the_filter_keeps_stays_the_choice(qapp: object, tmp_path: Path) -> None:
    view = _roster_view(tmp_path)
    view.character_list.setCurrentRow(1)

    view.character_filter.setText("ganaar")

    assert _shown(view) == ["Ganaar"]
    assert view._chosen_character() == "Ganaar", "the visible choice was kept"


def test_the_filter_survives_a_refresh_of_the_roster(qapp: object, tmp_path: Path) -> None:
    view = _roster_view(tmp_path)
    view.character_filter.setText("gug")

    view.refresh_characters()

    assert _shown(view) == ["Guglu"], "a refresh is not the filter's undo"


def test_typing_in_the_filter_does_not_switch_the_sub_tab(qapp: object, tmp_path: Path) -> None:
    """T221, again from the new field: keys typed into it type, they do not navigate."""
    view = _roster_view(tmp_path)
    view._tabs.setCurrentIndex(view._tabs.count() - 1)  # whatever page the lists sit on
    page = view._tabs.currentWidget()
    view.character_filter.setFocus()
    QTest.keyClicks(view.character_filter, "Gug")
    QTest.keyClick(view.character_filter, Qt.Key.Key_Backspace)
    pump_until(lambda: view.character_filter.text() == "Gu", "the keys were typed")

    assert view._tabs.currentWidget() is page, "typing in the filter stayed on the page"
    assert _shown(view) == ["Guglu"], "the narrowed list followed the keys, backspace included"


# -- the Bots browse ----------------------------------------------------------


class _Browser:
    """The bots browse seam, recording every page ask and its filter."""

    def __init__(self, names: tuple[str, ...] = ()) -> None:
        self.names = names
        self.pages: list[tuple[object, str]] = []

    def page(self, *, after: object = None, name_like: str = "", limit: int = 50) -> object:
        self.pages.append((after, name_like))
        names = (
            self.names
            if not name_like
            else tuple(name for name in self.names if name_like.lower() in name.lower())
        )
        return _BotPage(names=names, total=len(names))


class _BotPage:
    def __init__(self, names: tuple[str, ...], total: int) -> None:
        self.bots = tuple(_Bot(name) for name in names)
        self.total = total
        self.problem = ""
        self.next_after = None
        self.by_registry = 0
        self.by_prefix = len(names)


class _Bot:
    def __init__(self, name: str) -> None:
        self.name = name
        self.level = 9
        self.online = True
        self.source = "prefix"


def _browser_view(tmp_path: Path, browser: _Browser) -> object:
    from tests.test_controller_view import _Ps, _services

    services = replace(_services(_Ps(), tmp_path, []), bots=browser, play=_Play(characters=()))
    return _build(WOTLK, services)


def _build(entry: object, services: object) -> object:
    from yulon.ui import controller_view as controller_view_module

    return controller_view_module.ControllerView(
        entry, services, status_poll_ms=0, job_runner=run_inline  # type: ignore[arg-type]
    )


def test_typing_in_the_bots_filter_re_reads_the_first_page_a_beat_later(
    qapp: object, tmp_path: Path
) -> None:
    """A keystroke burst is one read: the timer resets, the last keystroke wins."""
    browser = _Browser()
    view = _browser_view(tmp_path, browser)
    assert browser.pages == [], "nothing reads until a filter is typed"

    view.bot_filter.setText("arm")
    view.bot_filter.setText("armor")
    pump_until(lambda: browser.pages != [], "the debounced read landed")

    assert browser.pages == [(None, "armor")], "one ask, the first page, the last keystroke"


def test_the_bots_filter_is_a_substring_not_a_prefix(qapp: object, tmp_path: Path) -> None:
    browser = _Browser()
    view = _browser_view(tmp_path, browser)
    view.bot_filter.setText("xar")
    view.filter_bots()
    assert browser.pages[-1][1] == "xar"
    # The statement the real reader builds carries both wildcards; pinned in
    # tests/test_botlist.py's pattern tests, so only the wiring is checked here.


def test_the_find_button_reads_now_without_waiting_for_the_beat(
    qapp: object, tmp_path: Path
) -> None:
    browser = _Browser()
    view = _browser_view(tmp_path, browser)

    view.bot_filter.setText("arm")
    view.filter_bots_button.click()

    assert browser.pages == [(None, "arm")], "Find reads now; the beat never ran again"


def test_the_bots_clear_button_empties_the_filter_and_restores_every_bot(
    qapp: object, tmp_path: Path
) -> None:
    browser = _Browser(names=("Guglu", "Rexxar"))
    view = _browser_view(tmp_path, browser)
    view.bot_filter.setText("gug")
    pump_until(lambda: browser.pages != [], "the filtered read landed")
    assert view.bot_list.count() == 1

    button = view.bot_filter.findChild(QToolButton)
    assert button is not None
    button.click()
    pump_until(lambda: len(browser.pages) >= 2, "the cleared read landed")

    assert view.bot_filter.text() == ""
    assert view.bot_list.count() == 2, "every bot is back"
