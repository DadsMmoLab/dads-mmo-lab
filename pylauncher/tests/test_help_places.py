"""T520: each game says where a player takes a problem, and the Server tab opens it.

The owner (2026-10-06): "add snapjaw discord, penqle repo and your bots repo
somewhere in yulon so they know where they could go for their specific issue
... also add on the other versions". The places are catalog data per game
(`CatalogEntry.help_places`); the Server tab's "Where to get help…" press opens
them in the fitted question box (T243), one link button per place with a line
saying what it is for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QScrollArea, QWidget

from tests.conftest import process_events
from tests.support_player_text import command_faults, text_faults
from tests.test_controller_view import ps  # noqa: F401 - the fixture, fakes `docker ps`
from yulon.catalog.catalog import HelpPlace, load_catalog
from yulon.ui import help_places
from yulon.ui.help_places import HelpPlacesBox
from yulon.ui.message_box import QUESTION_SCROLL

CATALOG = load_catalog()

YULON_ISSUES = "https://github.com/DadsMmoLab/dads-mmo-lab/issues"
SNAPJAW_DISCORD = "https://discord.gg/FQ2WfYD4Tz"
TORTOISEBOTS_THREAD = "https://discord.com/channels/1496531781149528287/1542647578548772954"
"""The TortoiseBots thread inside the Snapjaw Discord: it opens only for someone who has
joined the server, which is what its line tells the player (owner, 2026-10-06)."""
CENTURION_DISCORD = "https://discord.gg/YberzPtxwb"
CMANGOS_ISSUES = "https://github.com/cmangos/issues/issues"
"""cMaNGOS takes every bug, server and playerbots, here: mangos-tbc, mangos-classic and
playerbots have their own issue pages switched off (gh api, 2026-10-06)."""

EXPECTED: dict[str, list[tuple[str, str]]] = {
    "wow-tortoise": [
        ("Snapjaw Discord", SNAPJAW_DISCORD),
        ("TortoiseBots thread (Snapjaw Discord)", TORTOISEBOTS_THREAD),
        ("tortoise-wow (Penqle)", "https://github.com/tortoise-wow/tortoise-wow/issues"),
        ("Sagiroth/TortoiseBots", "https://github.com/Sagiroth/TortoiseBots/issues"),
        ("Yu'lon", YULON_ISSUES),
    ],
    "wow-unbound": [
        (
            "Dad's MMO Lab Discord, #wow-unbound",
            "https://discord.com/channels/1505690043044335749/1507088008837595226",
        ),
        ("AzerothCore", "https://github.com/azerothcore/azerothcore-wotlk/issues"),
        ("mod-playerbots", "https://github.com/mod-playerbots/mod-playerbots/issues"),
        ("Yu'lon", YULON_ISSUES),
    ],
    "wow-wotlk": [
        ("AzerothCore", "https://github.com/azerothcore/azerothcore-wotlk/issues"),
        ("mod-playerbots", "https://github.com/mod-playerbots/mod-playerbots/issues"),
        ("Yu'lon", YULON_ISSUES),
    ],
    "wow-tbc": [
        ("cMaNGOS TBC", CMANGOS_ISSUES),
        ("cMaNGOS playerbots", CMANGOS_ISSUES),
        ("Yu'lon", YULON_ISSUES),
    ],
    "wow-vanilla": [
        ("cMaNGOS Classic", CMANGOS_ISSUES),
        ("cMaNGOS playerbots", CMANGOS_ISSUES),
        ("Yu'lon", YULON_ISSUES),
    ],
    "wow-centurion": [
        ("Centurion Discord", CENTURION_DISCORD),
        # Issues are switched off on this repo (gh api, 2026-10-06): the CENTURION
        # branch page instead, the owner's link.
        (
            "TrinityCore112 (Centurion)",
            "https://github.com/thomasjteachey/TrinityCore112/tree/CENTURION",
        ),
        ("Yu'lon", YULON_ISSUES),
    ],
}


# -- the catalog ---------------------------------------------------------------


def test_every_shipped_game_is_listed_here() -> None:
    assert sorted(entry.id for entry in CATALOG.games) == sorted(EXPECTED)


@pytest.mark.parametrize("game_id", sorted(EXPECTED))
def test_each_game_names_its_places_in_order(game_id: str) -> None:
    places = CATALOG.get(game_id).help_places
    assert [(p.label, p.url) for p in places] == EXPECTED[game_id]


def test_every_game_ends_with_the_same_yulon_place() -> None:
    lasts = {entry.id: entry.help_places[-1] for entry in CATALOG.games}
    assert {p.url for p in lasts.values()} == {YULON_ISSUES}
    assert len({(p.label, p.purpose) for p in lasts.values()}) == 1, lasts
    assert "installing" in lasts["wow-wotlk"].purpose


def test_every_purpose_line_is_a_plain_sentence_for_a_player() -> None:
    faults = [
        (entry.id, place.purpose, text_faults(place.purpose) + command_faults(place.purpose))
        for entry in CATALOG.games
        for place in entry.help_places
        if text_faults(place.purpose) + command_faults(place.purpose)
        or not place.purpose.endswith(".")
        or "\n" in place.purpose
    ]
    assert faults == []


def test_the_thread_line_says_to_join_the_discord_first() -> None:
    thread = CATALOG.get("wow-tortoise").help_places[1]
    assert thread.url == TORTOISEBOTS_THREAD
    assert "join the Snapjaw Discord first" in thread.purpose


@pytest.mark.parametrize("url", [SNAPJAW_DISCORD, TORTOISEBOTS_THREAD, CENTURION_DISCORD])
def test_the_discord_addresses_pass_the_https_check(url: str) -> None:
    assert HelpPlace(label="A place", url=url, purpose="Questions.").url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/x/y",
        "https://user:secret@github.com/x/y",
        "github.com/x/y",
        "javascript:alert(1)",
        "",
        "http://discord.gg/FQ2WfYD4Tz",
        "discord.gg/FQ2WfYD4Tz",
    ],
)
def test_a_place_url_must_be_https(url: str) -> None:
    with pytest.raises(ValidationError):
        HelpPlace(label="A place", url=url, purpose="Bugs.")


@pytest.mark.parametrize("field", ["label", "purpose"])
def test_a_place_needs_its_label_and_its_line(field: str) -> None:
    data = {"label": "A place", "url": "https://example.org", "purpose": "Bugs."}
    data[field] = ""
    with pytest.raises(ValidationError):
        HelpPlace(**data)


# -- the dialog ----------------------------------------------------------------

PLACES = (
    HelpPlace(label="First", url="https://example.org/one", purpose="The first place."),
    HelpPlace(label="Second", url="https://example.org/two", purpose="The second place."),
)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[QUrl]:
    """The URLs the dialog asked the desktop to open, instead of opening them."""
    calls: list[QUrl] = []

    def fake_open(url: QUrl) -> bool:
        calls.append(QUrl(url))
        return True

    monkeypatch.setattr(help_places.QDesktopServices, "openUrl", staticmethod(fake_open))
    return calls


def _box(qapp: object, places: tuple[HelpPlace, ...] = PLACES) -> HelpPlacesBox:
    parent = QWidget()
    parent.resize(960, 640)
    box = HelpPlacesBox(parent, "WoW Test", places)
    box._test_parent = parent  # type: ignore[attr-defined]  # keep the parent alive
    box.show()
    process_events()
    return box


def test_the_box_lists_a_link_per_place_in_order_with_its_line(qapp: object) -> None:
    box = _box(qapp)
    assert [b.text() for b in box.link_buttons] == ["First", "Second"]
    assert box.purpose_texts() == ["The first place.", "The second place."]
    for button in box.link_buttons:
        assert button.isVisibleTo(box)
    assert box.windowTitle() == "Where to get help"
    box.close()


def test_every_link_is_a_tab_stop_and_tab_reaches_each(qapp: object) -> None:
    box = _box(qapp)
    assert len(box.link_buttons) == 2
    for button in box.link_buttons:
        assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
        assert button.isEnabled()
    reached: set[int] = set()
    for _ in range(12):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        process_events(5)
        reached.add(id(QApplication.focusWidget()))
    missing = [b.text() for b in box.link_buttons if id(b) not in reached]
    assert missing == []
    box.close()


def test_a_link_press_opens_its_url_and_keeps_the_box_open(
    qapp: object, opened: list[QUrl]
) -> None:
    box = _box(qapp)
    box.link_buttons[1].click()
    process_events()
    assert opened == [QUrl("https://example.org/two")]
    assert box.isVisible(), "a link press closed the box"
    box.close()


def test_a_browser_that_will_not_open_says_the_address(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(help_places.QDesktopServices, "openUrl", staticmethod(lambda url: False))
    monkeypatch.setattr(
        help_places, "show_warning", lambda parent, title, text: said.append((title, text))
    )
    box = _box(qapp)
    box.link_buttons[0].click()
    process_events()
    assert len(said) == 1 and "https://example.org/one" in said[0][1]
    box.close()


def test_close_is_the_one_standard_button_and_escape_closes(qapp: object) -> None:
    box = _box(qapp)
    standard = [b for b in box.buttons()]
    assert [box.standardButton(b) for b in standard] == [QMessageBox.StandardButton.Close]
    assert box.escapeButton() is box.button(QMessageBox.StandardButton.Close)
    QApplication.sendEvent(
        box, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )
    process_events()
    assert not box.isVisible()


def test_the_box_fits_the_screen_with_every_link_inside_it(qapp: object) -> None:
    tortoise = CATALOG.get("wow-tortoise").help_places
    box = _box(qapp, tortoise)
    assert [b.text() for b in box.link_buttons] == [p.label for p in tortoise]
    room = box.screen().availableGeometry()
    frame = box.frameGeometry()
    assert room.contains(frame), (room, frame)
    for button in [*box.link_buttons, *box.buttons()]:
        top_left = button.mapTo(box, button.rect().topLeft())
        assert box.rect().contains(top_left), button.text()
    box.close()


def test_a_list_longer_than_the_screen_scrolls_and_tab_shows_each_link(qapp: object) -> None:
    """T243's rule holds for the links: the box stays on screen, Close with it, and the
    links scroll, each brought into view as Tab lands on it."""
    many = tuple(
        HelpPlace(label=f"Place {n}", url=f"https://example.org/{n}", purpose="A place.")
        for n in range(40)
    )
    box = _box(qapp, many)
    room = box.screen().availableGeometry()
    assert room.contains(box.frameGeometry()), (room, box.frameGeometry())
    close = box.button(QMessageBox.StandardButton.Close)
    assert room.contains(close.mapToGlobal(close.rect().bottomRight()))
    scroll = box.findChild(QScrollArea, QUESTION_SCROLL)
    assert scroll is not None
    viewport = scroll.viewport()
    hidden_at_first = [
        b
        for b in box.link_buttons
        if not viewport.rect().contains(b.mapTo(viewport, b.rect().center()))
    ]
    assert hidden_at_first, "40 places all showed at once: the case does not overflow"
    seen: set[int] = set()
    for _ in range(60):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        process_events(5)
        focused = QApplication.focusWidget()
        if focused in box.link_buttons:
            assert viewport.rect().contains(
                focused.mapTo(viewport, focused.rect().center())
            ), f"{focused.text()} has the focus out of view"
            seen.add(id(focused))
    assert len(seen) == 40
    box.close()


def test_show_help_places_lists_the_games_own_places(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(HelpPlacesBox, "exec", lambda self: seen.append(self) or 0)
    help_places.show_help_places(None, CATALOG.get("wow-wotlk"))
    assert [b.text() for b in seen[0].link_buttons] == [label for label, _ in EXPECTED["wow-wotlk"]]


# -- the Server tab --------------------------------------------------------------


@pytest.mark.usefixtures("ps")
def test_the_server_tab_press_opens_this_games_places(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_controller_view import TORTOISE, _section_of, _server_view
    from yulon.ui import controller_view

    asked: list[str] = []
    monkeypatch.setattr(
        controller_view, "show_help_places", lambda parent, entry: asked.append(entry.id)
    )
    view = _server_view(TORTOISE, tmp_path)
    button = view.help_button
    assert button.text() == "Where to get help…"
    assert isinstance(button, QPushButton) and not button.isHidden()
    assert _section_of(view, button) == "Help"
    button.click()
    assert asked == ["wow-tortoise"]
