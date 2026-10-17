"""The "Quit anyway" way out of a refused quit (T690).

A refusal with no way past it is a trap: a flag stuck by a bug elsewhere would make
Yu'lon impossible to close. The box names what is running, says plainly what quitting
anyway leaves half-done, and keeps "Keep waiting" as the default.
"""

from __future__ import annotations

from typing import Any

from yulon.ui import quit_anyway

REASON = "A restore is writing into this server's databases on its Maintenance tab."


def test_the_box_names_the_work_and_the_cost_and_waiting_is_the_default(qapp: Any) -> None:
    box, buttons = quit_anyway.build_box(REASON)
    try:
        assert set(buttons) == {"wait", "quit"}
        assert buttons["wait"].text() == "Keep waiting"
        assert buttons["quit"].text() == "Quit anyway"
        assert box.defaultButton() is buttons["wait"]
        assert box.escapeButton() is buttons["wait"]
        text = box.text()
        assert REASON in text
        assert "half-done" in text and "ends Yu'lon at once" in text
    finally:
        box.deleteLater()


def test_ask_answers_true_only_for_quit_anyway(qapp: Any, monkeypatch: Any) -> None:
    for pressed, expected in (("quit", True), ("wait", False)):
        monkeypatch.setattr(
            quit_anyway, "_run", lambda _box, buttons, which=pressed: buttons[which]
        )
        assert quit_anyway.ask(None, REASON) is expected


def test_a_restore_is_said_to_carry_on_in_docker_not_to_be_cut_off(qapp: Any) -> None:
    """Quit anyway does not stop the restore's `docker exec`; saying "cut off" would be false.

    Mutation: return `COST` for every kind from `quit_anyway.cost_of()`.
    """
    from yulon import teardown

    for kind in (teardown.RESTORE, teardown.UPDATE_BACKUP, teardown.MOVE):
        box, _buttons = quit_anyway.build_box(teardown.Refusal(REASON, kind))
        try:
            text = box.text()
            assert "carries on" in text and "Docker" in text, (kind, text)
            assert "not start Yu'lon again" in text, (kind, text)
            assert "cuts that work off" not in text, (kind, text)
        finally:
            box.deleteLater()


def test_work_that_really_is_cut_off_says_so(qapp: Any) -> None:
    from yulon import teardown

    for reason in (
        teardown.Refusal(REASON, teardown.BACKUP),
        teardown.Refusal(REASON, teardown.NETWORK),
        REASON,  # a plain string: a refusal that carries no kind is the cautious one
    ):
        box, _buttons = quit_anyway.build_box(reason)
        try:
            assert "cuts that work off" in box.text(), box.text()
        finally:
            box.deleteLater()
