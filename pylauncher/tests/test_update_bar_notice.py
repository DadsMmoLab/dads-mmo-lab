"""T179 Task 5 fix round 1 (M1): a notice never covers an update offer or an announcement.

`UpdateBar.offer_notice()` is how the start-up sweep of temporary client copies
speaks. It is said only while the bar is idle; over an offer or a message it waits
and is said when the bar clears, and an offer arriving over it puts it back to wait.
"""

from __future__ import annotations

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
