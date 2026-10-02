"""T179 Task 5 fix round 1 (M1): a notice never covers an update offer or an announcement.

`UpdateBar.offer_notice()` is how the start-up sweep of temporary client copies
speaks. It is said only while the bar is idle; over an offer or a message it waits
and is said when the bar clears, and an offer arriving over it puts it back to wait.
"""

from __future__ import annotations

import pytest

from tests.conftest import pump_until
from yulon.ui.widgets import update_bar as update_bar_module
from yulon.ui.widgets.update_bar import UpdateBar
from yulon.update import UpdateCheck

OFFER = UpdateCheck(
    "0.8.66-Public", "v0.8.70-Public", True, "https://github.com/x/y/releases/tag/v0.8.70-Public"
)
NOTICE = "Yu'lon could not remove a temporary copy of a game client yet."


def test_an_idle_bar_says_the_notice(qapp: object) -> None:
    bar = UpdateBar()
    bar.offer_notice(NOTICE)
    assert not bar.isHidden() and bar.text() == NOTICE
    assert bar.details_button.isHidden()


def test_a_notice_waits_behind_an_offer_and_is_said_when_the_bar_clears(qapp: object) -> None:
    bar = UpdateBar()
    bar.show_update(OFFER)
    offer = bar.text()
    bar.offer_notice(NOTICE)
    assert bar.text() == offer, "the notice covered the update offer"
    assert not bar.details_button.isHidden()
    bar.clear()
    assert not bar.isHidden() and bar.text() == NOTICE
    bar.clear()
    assert bar.isHidden()


def test_a_notice_waits_behind_the_updated_announcement(qapp: object) -> None:
    bar = UpdateBar()
    bar.show_message("Updated to Yu'lon 0.8.70.")
    bar.offer_notice(NOTICE)
    assert bar.text() == "Updated to Yu'lon 0.8.70."


def test_an_offer_arriving_over_a_notice_wins_and_the_notice_comes_back(qapp: object) -> None:
    bar = UpdateBar()
    bar.offer_notice(NOTICE)
    bar.show_update(OFFER)
    assert bar.text() != NOTICE
    bar.clear()
    assert bar.text() == NOTICE and not bar.isHidden()


# -- fix round 2: a notice is recorded when SHOWN, and announcements clear themselves


def test_a_notice_waiting_behind_updated_to_is_not_recorded_until_it_shows(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update_bar_module, "FADE_MS", 20)
    shown: list[str] = []
    bar = UpdateBar()
    bar.show_message("Updated to Yu'lon 0.8.70.", fade=True)
    bar.offer_notice(NOTICE, on_shown=lambda: shown.append("recorded"))
    assert shown == [], "recorded while it was still waiting"
    assert bar.text() == "Updated to Yu'lon 0.8.70."

    pump_until(lambda: bar.text() == NOTICE, "the announcement never cleared for the notice")
    assert shown == ["recorded"]
    assert not bar.isHidden()


def test_a_fading_message_clears_itself_and_an_ordinary_one_stays(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update_bar_module, "FADE_MS", 20)
    bar = UpdateBar()
    bar.show_message("You have the newest version (0.8.70).", fade=True)
    assert bar.fading()
    pump_until(lambda: bar.isHidden(), "the answer to a check never cleared")

    stays = UpdateBar()
    stays.show_message("Could not check for updates: offline")
    assert not stays.fading()


def test_an_old_fade_does_not_clear_what_was_said_after_it(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update_bar_module, "FADE_MS", 20)
    bar = UpdateBar()
    bar.show_message("You have the newest version.", fade=True)
    bar.show_update(OFFER)
    offer = bar.text()
    from PySide6.QtCore import QCoreApplication, QThread

    for _ in range(10):
        QThread.msleep(10)
        QCoreApplication.processEvents()
    assert bar.text() == offer and not bar.isHidden(), "the old timer cleared the offer"


def test_a_notice_is_recorded_once_even_when_it_steps_aside_and_returns(qapp: object) -> None:
    shown: list[str] = []
    bar = UpdateBar()
    bar.offer_notice(NOTICE, on_shown=lambda: shown.append("recorded"))
    bar.show_update(OFFER)
    bar.clear()
    assert bar.text() == NOTICE
    assert shown == ["recorded"]
