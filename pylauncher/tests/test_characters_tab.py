"""Tests for the Characters tab (8.4a) — what it draws and what it promises.

The tab's own rules, from this box's line: every action is drawn only where the
tree has the command, every button names the selected character in its label,
and every press reports all three outcomes.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QMetaObject, QObject, Qt, Slot

from tests.conftest import HANG_BOUND_MS, pump_until
from yulon import play as play_module
from yulon.catalog.catalog import load_catalog
from yulon.play import Character
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import ThreadedJobRunner, run_inline, threaded_job_runner

WOTLK = load_catalog().get("wow-wotlk")
TORTOISE = load_catalog().get("wow-tortoise")
"""The tree whose console has no route to an arbitrary level (8.4d)."""

UNMEASURED = WOTLK.model_copy(update={"play": None})
"""A tree with no Play block at all, synthesised because no shipped one is left.

It was Vanilla until 8.4c measured that one and Tortoise until 8.4d measured
this one. `model_copy` keeps the id, so `ControllerServices.for_entry` still
finds a factory: this stands for a game whose 8.4 box has not been done, not
for one this build cannot manage.
"""


@pytest.fixture(autouse=True)
def _inline_jobs(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The session's one offscreen QApplication, and jobs that run inline.

    `qapp` is depended on rather than requested per test: a view built with no
    QApplication takes the interpreter down, which reads as a test file that
    printed nothing at all.
    """
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


class _Play:
    """A stand-in for `InstallPlay`, recording what the tab asked it to do."""

    def __init__(
        self, *, characters: tuple[Character, ...] = (), pieces: int = 19, cap: int = 12
    ) -> None:
        self.characters = characters
        self.pieces = pieces
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.mail_item_cap = cap
        self.answer = _Outcome(True, text="done")

    def listing(self) -> tuple[Character, ...]:
        self.calls.append(("listing", ()))
        return self.characters

    def gear_set_size(self, character: str) -> tuple[int, int]:
        """Pieces, and the mails they take AT THIS TREE'S CAP.

        The eleven and the twelve were written in here when TBC and WotLK were
        the only measured trees, and they made this fake answer the same way
        whatever tree it stood for: a two-piece set came back as one mail on a
        server whose cap is one (Vanilla, 8.4c). The arithmetic is spelled out
        rather than imported from `yulon.play`, so this stays a check on the tab
        and not a copy of the code under test.
        """
        self.calls.append(("gear_set_size", (character,)))
        cap = self.mail_item_cap
        return (self.pieces, (self.pieces + cap - 1) // cap if self.pieces else 0)

    def teleport(self, character: str, location: str) -> object:
        self.calls.append(("teleport", (character, location)))
        return self.answer

    def set_level(self, character: str, level: int) -> object:
        self.calls.append(("set_level", (character, level)))
        return self.answer

    def rename(self, character: str) -> object:
        self.calls.append(("rename", (character,)))
        return self.answer

    def revive(self, character: str) -> object:
        self.calls.append(("revive", (character,)))
        return self.answer

    def mail_gold(self, character: str, *, gold: int, subject: str, body: str) -> object:
        self.calls.append(("mail_gold", (character, gold)))
        return self.answer

    def mail_items(self, character: str, *, items, subject: str, body: str) -> object:
        self.calls.append(("mail_items", (character, items)))
        return self.answer

    def send_gear_set(self, character: str, *, to: str, subject: str, body: str) -> object:
        self.calls.append(("send_gear_set", (character, to)))
        return self.answer

    def find_items(self, text: str) -> tuple[object, ...]:
        self.calls.append(("find_items", (text,)))
        return ()


class _Outcome:
    def __init__(self, done: bool, text: str = "", problem: str = "") -> None:
        self.done = done
        self.text = text
        self.problem = problem
        self.indeterminate = False


def _view(tmp_path: Path, entry=WOTLK, play=None) -> ControllerView:
    services = ControllerServices.for_entry(entry, tmp_path / entry.id)
    services.play = play
    return ControllerView(entry, services, status_poll_ms=0)


def _people() -> tuple[Character, ...]:
    return (
        Character(1, "Guglu", 78, True, "ADMIN"),
        Character(2, "Ganaar", 7, False, "PLAYER"),
    )


# -- what is drawn ----------------------------------------------------------


def test_a_tree_that_has_not_measured_its_play_block_gets_a_sentence(tmp_path: Path) -> None:
    """Not a disabled button: a button that cannot work is a promise the tab
    cannot keep, and 8.4d is the box that makes it work there.

    The entry used to be Tortoise, because it was the one whose block was
    genuinely absent. 8.4d measured it, so the premise moved to a synthesised
    entry rather than staying on a tree that has since been measured: Vanilla
    stood here until 8.4c and, once its `play` was not None, the fixture said
    "this tree has not measured its block" about a tree that had, and passed
    anyway because the test forces `play=None`. A fixture that passes on a false
    premise is testing the argument, not the tree.
    """
    view = _view(tmp_path, UNMEASURED, play=None)

    assert view.character_list.isVisibleTo(view) is False
    assert UNMEASURED.name in view.character_report.text()
    assert UNMEASURED.play is None, "the premise this test is about"


def test_the_actions_wait_for_a_character_to_be_chosen(tmp_path: Path) -> None:
    view = _view(tmp_path, play=_Play(characters=_people()))

    for button in view.character_buttons():
        assert button.isEnabled() is False, button.text()


def test_every_button_names_the_character_it_would_act_on(tmp_path: Path) -> None:
    """This box's own rule. "Revive" is a button somebody presses believing it
    acts on the row they are looking at; "Revive Guglu" is one they can check."""
    view = _view(tmp_path, play=_Play(characters=_people()))
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    for button in view.character_buttons():
        assert "Guglu" in button.text(), button.text()


def test_choosing_a_different_character_renames_every_button(tmp_path: Path) -> None:
    view = _view(tmp_path, play=_Play(characters=_people()))
    view.refresh_characters()
    view.character_list.setCurrentRow(0)
    view.character_list.setCurrentRow(1)

    for button in view.character_buttons():
        assert "Ganaar" in button.text(), button.text()


def test_the_gear_button_says_how_many_mails_before_it_is_pressed(tmp_path: Path) -> None:
    """The definition of done: "a nineteen-piece gear set sends the number of
    mails the button promised". The promise has to exist to be kept."""
    view = _view(tmp_path, play=_Play(characters=_people(), pieces=19))
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    assert "19" in view.send_gear_button.text()
    assert "2" in view.send_gear_button.text(), view.send_gear_button.text()


def test_a_character_wearing_nothing_leaves_the_gear_button_alone(tmp_path: Path) -> None:
    view = _view(tmp_path, play=_Play(characters=_people(), pieces=0))
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    assert view.send_gear_button.isEnabled() is False


# -- what is pressed --------------------------------------------------------


def test_each_action_reaches_the_seam_with_the_chosen_character(tmp_path: Path) -> None:
    play = _Play(characters=_people())
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    view.teleport_where.setText("Stormwind")
    view.teleport_character()
    view.new_level.setValue(60)
    view.set_character_level()
    view.rename_character()
    view.revive_character()
    view.gold_amount.setValue(5)
    view.mail_gold()

    # The list and the gear size are re-read after every success on purpose --
    # a level change moves the row somebody is looking at -- so what this
    # asserts is the ACTIONS, in order, with the reads filtered out.
    reads = {"listing", "gear_set_size"}
    assert [name for name, _ in play.calls if name not in reads] == [
        "teleport",
        "set_level",
        "rename",
        "revive",
        "mail_gold",
    ]
    assert ("teleport", ("Guglu", "Stormwind")) in play.calls
    assert ("set_level", ("Guglu", 60)) in play.calls
    assert ("mail_gold", ("Guglu", 5)) in play.calls


def test_a_refusal_is_shown_and_does_not_look_like_a_success(tmp_path: Path) -> None:
    play = _Play(characters=_people())
    play.answer = _Outcome(False, problem="Character not found")
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    view.revive_character()

    assert "Character not found" in view.character_report.text()


def test_a_success_says_what_the_server_said(tmp_path: Path) -> None:
    play = _Play(characters=_people())
    play.answer = _Outcome(True, text="You revive Guglu.")
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    view.revive_character()

    assert "revive" in view.character_report.text().lower()


def test_the_list_is_not_re_read_the_instant_the_server_answers(tmp_path: Path) -> None:
    """Measured on the live server, 2026-09-07: the answer beats the write.

        level 55 -> 58: the server answered in 0.18s, the row changed after 0.30s
        level 58 -> 59: the server answered in 0.15s, the row changed after 0.26s

    So a list re-read the moment the command answers shows a person the state
    BEFORE the thing they just did — "You change the level of Aevret to 60"
    above a row that still says 55, which reads as the action having failed.

    The refresh is scheduled instead. What this asserts is the absence: no read
    between the press and the answer being shown.
    """
    play = _Play(characters=_people())
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    view.character_list.setCurrentRow(0)
    play.calls.clear()

    view.revive_character()

    assert [name for name, _ in play.calls] == ["revive"], play.calls
    assert view.character_report.text().strip(), "the server's own sentence is shown at once"


def test_the_gear_button_counts_in_english(tmp_path: Path) -> None:
    """ "Send Aevret's 12 worn items (1 mails)" is what the live gate photographed.

    One mail is a mail. It is a small thing and it is on the front of a button
    somebody is about to press, which is where small things are read.
    """
    view = _view(tmp_path, play=_Play(characters=_people(), pieces=12))
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    assert "1 mail)" in view.send_gear_button.text(), view.send_gear_button.text()

    view = _view(tmp_path, play=_Play(characters=_people(), pieces=19))
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    assert "2 mails)" in view.send_gear_button.text(), view.send_gear_button.text()


def test_on_a_one_item_per_mail_tree_the_button_promises_a_mail_per_piece(
    tmp_path: Path,
) -> None:
    """8.4c's own clause: "a two-item send arrives as two mails and the button
    says so before the press".

    The sentence needs no new shape for it — the tab has counted in mails since
    8.4a — but until this test the FIXTURE could not express a tree that is not
    TBC: it computed `(pieces + 11) // 12` and would have answered "1 mail" for
    a two-piece set on a server that sends two, whatever the tab did.

    The cap is 1 here because `MAX_MAIL_ITEMS` is 1 on the Vanilla install
    (read on m910q, 2026-09-07, `src/game/Mails/Mail.h:49`).
    """
    view = _view(tmp_path, play=_Play(characters=_people(), pieces=2, cap=1))
    view.refresh_characters()
    view.character_list.setCurrentRow(0)

    said = view.send_gear_button.text()
    assert "2 worn items" in said, said
    assert "(2 mails)" in said, said


def test_revive_is_not_offered_offline_on_a_tree_that_has_not_measured_it(
    tmp_path: Path,
) -> None:
    """WotLK, whose entry says nothing about an offline revive.

    It said something for a while, and what it said was wrong. 8.4a and 8.4b
    read `characters.health` before and after -- 0 and 0 -- and concluded the
    command does nothing to a character who is not logged in, so the button was
    disabled for every tree by a constant in this file. 8.4c watched the CORPSE
    instead, on the Vanilla server, and it went; the offline branch is
    `ConvertCorpseForPlayer`, "will resurrected at login without corpse", which
    is a real effect in exactly the column health is not.

    So the rule is now the entry's rather than this tab's, and an unmeasured
    tree keeps the greyed button -- for the honest reason, which is that nobody
    has run the command there and watched. Every other action works offline: the
    teleport's own help says "Character can be offline".
    """
    people = (
        Character(1, "Guglu", 78, True, "ADMIN"),
        Character(2, "Ganaar", 7, False, "PLAYER"),
    )
    view = _view(tmp_path, play=_Play(characters=people))
    view.refresh_characters()

    view.character_list.setCurrentRow(0)
    assert view.revive_button.isEnabled() is True, "an online character can be revived"

    view.character_list.setCurrentRow(1)
    assert view.revive_button.isEnabled() is False
    assert "logged in" in view.revive_button.text(), view.revive_button.text()
    for other in view.character_buttons():
        if other is not view.revive_button:
            assert other.isEnabled() is True, other.text()


def test_the_list_refresh_after_an_action_is_bounded_rather_than_a_single_guess(
    tmp_path: Path,
) -> None:
    """The review's finding about the 750ms delay, and it is a fair one.

    One fixed delay measured on one server is a guess about every other: a
    slower box, a bigger world or a stalled disk can take longer, and a single
    scheduled read then leaves the list stale indefinitely with nothing to say
    so. What replaces it is a short bounded sequence of re-reads that stops as
    soon as the list changes -- and a generation token, so an older refresh
    landing late cannot overwrite a newer one.
    """
    play = _Play(characters=_people())
    view = _view(tmp_path, play=play)

    assert view.refresh_attempts_after_an_action() > 1
    assert view.refresh_attempts_after_an_action() <= 6

    # a late result from an older generation is discarded
    view.refresh_characters()
    stale_generation = view._character_generation
    view.refresh_characters()
    view._characters_listed_at(stale_generation, ())
    assert view.character_list.count() == 2, "a late older refresh emptied the list"


def test_the_character_list_is_written_on_the_gui_thread_by_a_real_threaded_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T97: "crashes if you try to add gold to owned bots" (Rixter, Tortoise on Windows).

    The read ran on a worker thread, as it should; its ANSWER was handed to a
    lambda, and `job.py` says what that means -- a plain callable is delivered
    on the worker thread. So `clear()` and one `addItem()` per character ran
    off the GUI thread while the GUI thread painted the same list. Every
    successful action schedules up to four such re-reads, and a bot server has
    hundreds of rows. Measured on m910q, 2026-09-23: pressing Send gold on the
    real tab segfaulted the app in 4 of 5 runs, with the worker's frame in
    `self.character_list.clear()`, while the worldserver stayed up.

    Every other test here runs jobs inline, where the question cannot arise, so
    this one uses the real runner and records the thread each write lands on.
    """
    play = _Play(characters=_people())
    runner: list[ThreadedJobRunner] = []

    def real_runner(parent: object) -> ThreadedJobRunner:
        runner.append(ThreadedJobRunner(parent))  # type: ignore[arg-type]
        return runner[0]

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", real_runner)
    view = _view(tmp_path, play=play)
    writes: list[tuple[str, bool]] = []
    listing = view.character_list
    for name in ("clear", "addItem"):
        real = getattr(listing, name)

        def recorded(*args: object, _name: str = name, _real: object = real) -> object:
            writes.append((_name, threading.current_thread() is threading.main_thread()))
            return _real(*args)  # type: ignore[operator]

        setattr(listing, name, recorded)

    view.refresh_characters()
    pump_until(lambda: listing.count() == len(_people()), "the refreshed list")
    assert runner[0].wait(HANG_BOUND_MS) is True

    assert writes, "the refresh never wrote the list"
    off_thread = [name for name, on_gui in writes if not on_gui]
    assert off_thread == [], f"the character list was written from a worker thread: {off_thread}"


def test_revive_is_offered_offline_where_the_tree_measured_that_it_works(
    tmp_path: Path,
) -> None:
    """Vanilla, measured live on m910q 2026-09-07 (8.4c).

    An offline character with a corpse kept it through twelve untouched seconds,
    lost it within twelve seconds of the command, and never logged in. The entry
    carries that as `play.revive_offline`, and the button follows the entry.
    """
    vanilla = load_catalog().get("wow-vanilla")
    assert vanilla.play is not None and vanilla.play.revive_offline is True

    view = _view(tmp_path, entry=vanilla, play=_Play(characters=_people(), cap=1))
    view.refresh_characters()

    view.character_list.setCurrentRow(1)
    assert view.character_list.currentItem().text().endswith("PLAYER")
    assert view.revive_button.isEnabled() is True, view.revive_button.text()
    assert "logged in" not in view.revive_button.text(), view.revive_button.text()
    assert "Ganaar" in view.revive_button.text(), view.revive_button.text()


def test_a_gear_set_that_cannot_be_read_says_why_rather_than_wearing_nothing(
    tmp_path: Path,
) -> None:
    """Two characters called Joleta on the live Vanilla server, 2026-09-07.

    The read raises rather than answering a wrong set, and the tab used to
    swallow that into "Joleta is wearing nothing" -- a false sentence about the
    character in place of a true one about the server. The button is still
    disabled, because there is still nothing safe to press; what changed is that
    it now says which of the two things is wrong.
    """
    play = _Play(characters=_people())

    def refuse(character: str) -> tuple[int, int]:
        raise play_module.Ambiguous(
            f"2 characters here are called {character}",
            "(guids 255, 446), and no command this app sends can tell them apart.",
        )

    play.gear_set_size = refuse  # type: ignore[method-assign]
    view = _view(tmp_path, play=play)
    view.refresh_characters()

    view.character_list.setCurrentRow(0)

    said = view.send_gear_button.text()
    assert view.send_gear_button.isEnabled() is False
    assert "2 characters" in said, said
    assert "wearing nothing" not in said, said
    # Short enough to read on a button, with the whole of it a hover away: the
    # first version put all 180 characters on the label and it ran off the end
    # of the window.
    assert len(said) < 60, said
    assert "guids 255, 446" in view.send_gear_button.toolTip(), view.send_gear_button.toolTip()


def test_a_gear_read_that_fails_some_other_way_still_does_not_claim_it_is_naked(
    tmp_path: Path,
) -> None:
    """A dead database is not a naked character either.

    The catch-all used to answer (0, 0), which the tab drew as "is wearing
    nothing" -- so a server that could not be read looked exactly like a
    character with no gear.
    """
    play = _Play(characters=_people())

    def explode(character: str) -> tuple[int, int]:
        raise RuntimeError("the database went away")

    play.gear_set_size = explode  # type: ignore[method-assign]
    view = _view(tmp_path, play=play)
    view.refresh_characters()

    view.character_list.setCurrentRow(0)

    said = view.send_gear_button.text()
    assert view.send_gear_button.isEnabled() is False
    assert "wearing nothing" not in said, said
    assert "Guglu" in said, said


# -- 8.4d: a group that is absent AND says why -------------------------------


def _in_the_layout(widget: object) -> bool:
    """Whether the form added a row for this control."""
    parent = widget.parentWidget()
    return parent.layout().indexOf(widget) >= 0


def _shown(widget: object) -> bool:
    """Whether this control would paint itself inside the group box that owns it.

    A widget left OUT of the form still has that group box as its PARENT, and a
    parented widget nobody laid out paints at the corner of it -- so "no row was
    added" is not by itself "no control is on screen", and hiding it is a second
    thing the tab has to do.

    Measured against the parent rather than the view, because `isVisibleTo(view)`
    cannot answer it at all: a QTabWidget hides every page but the current one,
    so that probe is False for every widget on this tab whether the tab drew it
    or not.

    **This is a separate function, and its callers assert it separately, because
    the two halves ANDed together proved nothing.** 8.4d's review deleted the
    two `setVisible(False)` lines in `_build_characters_tab`, purged the
    bytecode on both sides, and all 21 tests in this file still passed: where
    the control is absent `indexOf` is already -1, so an `and` short-circuits
    before the visibility half is ever reached, and the half that guards the
    corner-painting bug was dead. Re-run against the same mutation on
    2026-09-08, with the assertions split, it fails.
    """
    parent = widget.parentWidget()
    return widget.isVisibleTo(parent)


def _drawn(widget: object) -> bool:
    """Both halves at once, for the places that want the whole answer.

    Never the ONLY assertion about a control the tab is supposed to withhold --
    see `_shown`.
    """
    return _in_the_layout(widget) and _shown(widget)


def test_the_set_level_control_is_drawn_exactly_where_the_tree_has_the_command(
    tmp_path: Path,
) -> None:
    """Both directions, walking every game in the catalog.

    This is the box's own clause and it is written as a walk rather than as two
    named entries on purpose: the failure it guards is not "Tortoise draws the
    button", it is "the tab decides by id". A walk cannot be satisfied by an
    `if entry.id == "wow-tortoise"` -- that would pass here today and be wrong
    for the fifth game -- and it fails the day a tree's block changes its mind
    without the tab following.

    The tallies at the end are what stop it passing vacuously: a catalog where
    every tree has a level command, or none has, would make one direction of
    this assert nothing at all.
    """
    with_control, without_control = [], []
    for entry in load_catalog().games:
        if entry.play is None:  # pragma: no cover - none shipped since 8.4d
            continue
        view = _view(
            tmp_path, entry, play=_Play(characters=_people(), cap=entry.play.mail_item_cap)
        )
        view.refresh_characters()
        view.character_list.setCurrentRow(0)
        has_command = entry.play.set_level_command is not None
        (with_control if has_command else without_control).append(entry.id)

        assert _in_the_layout(view.set_level_button) is has_command, entry.id
        assert _in_the_layout(view.new_level) is has_command, entry.id
        # Separately, and not folded into the line above with an `and`: where
        # the control is absent `_in_the_layout` is already False, so an ANDed
        # probe can never reach this one and a tab that added no row but hid
        # nothing would pass. That mutant survived the first version of this
        # file (see `_shown`).
        assert _shown(view.set_level_button) is has_command, entry.id
        assert _shown(view.new_level) is has_command, entry.id
        assert (view.set_level_button in view.character_buttons()) is has_command, entry.id
        assert _in_the_layout(view.set_level_absent) is not has_command, entry.id
        assert _shown(view.set_level_absent) is not has_command, entry.id
        if has_command:
            assert view.set_level_absent.text() == "", entry.id
        else:
            assert view.set_level_absent.text() == entry.play.set_level_absent_reason, entry.id

    assert with_control, "no tree drew the control, so one direction proved nothing"
    assert without_control, "no tree withheld it, so the other direction proved nothing"


def test_where_the_control_is_absent_the_sentence_names_what_the_server_can_do(
    tmp_path: Path,
) -> None:
    """The half of the box a walk cannot check: that the sentence is about what
    EXISTS.

    "the group is replaced by a sentence naming what does exist rather than one
    implying nothing does". A generic walk can only assert that some sentence is
    drawn -- "Not supported" would satisfy it. This asserts the words, once, on
    the tree they were measured from, every one of them re-read on m910q on
    2026-09-08 against `~/tortoise-server/src/tortoise-wow` and that install's
    own `etc/mangosd.conf`:

    * `reset level` is `{ "level", SEC_ADMINISTRATOR, true, ... }` -- console
      allowed -- at `src/game/Chat/Chat.cpp:653`, and its handler
      (`Commands.cpp:3814`) takes only a `Player**` through `ExtractPlayerTarget`
      at `:3817`, so the character has to be logged in.
    * the level it writes is `CONFIG_UINT32_START_PLAYER_LEVEL`
      (`Commands.cpp:3824-3826`), and the NUMBER that key currently holds is
      `StartPlayerLevel = 1` at `etc/mangosd.conf:714` -- a line the owner of
      any install can edit. So the sentence must NOT say one: finding 5 of the
      review, and "present tense rots" -- shipped data that names a live
      config value is wrong the day somebody changes it, silently.

    The sentence itself used to say this console has NO command that puts a
    character at a level you pick, and an adversarial review refuted that:
    `.rndbot` is AllowConsole true (`Chat.cpp:1012`) and its dispatch reaches
    `PlayerbotMgr::CreateBot`, which parses `level=<n>` and calls `SetLevel`.
    The button is still rightly absent -- nothing console-legal moves an
    EXISTING character to a chosen level -- so the sentence draws that
    distinction rather than denying the command exists, and the two
    assertions below hold it to both halves.

    Two of these assertions are the same words `test_the_sentence_in_place_of_
    the_level_control_names_what_this_fork_has` makes about the catalog string.
    That is on purpose and it is not the same claim: that one says the
    measurement is written down, this one says the tab DRAWS it -- a view that
    fell back to a phrase of its own would pass that test and fail this.
    """
    view = _view(tmp_path, TORTOISE, play=_Play(characters=_people(), cap=1))

    said = view.set_level_absent.text()

    assert "reset level" in said, said
    assert "logged in" in said, said
    assert "configured starting level" in said, said
    assert "level 1" not in said, said
    # The route the review found, and the reason the sentence is narrower than
    # it was: `rndbot` IS console-allowed (`Chat.cpp:1012`) and
    # `rndbot create level=<n>` reaches `CreateBot`, which parses `level=` and
    # calls `SetLevel` (`PlayerbotMgr.cpp:2389`, `:2497-2510`). What it cannot
    # do is move a character that already exists, and that is the true clause.
    assert "rndbot create level=" in said, said
    assert "existing character" in said, said
    assert "NEW character" in said, said
    assert _in_the_layout(view.set_level_absent) is True
    assert _shown(view.set_level_absent) is True
    assert _in_the_layout(view.set_level_button) is False
    assert _shown(view.set_level_button) is False


def test_the_at_login_rename_is_withheld_offline_where_it_would_destroy_the_name(
    tmp_path: Path,
) -> None:
    """The fork's other trap, and it is worse than an absent command.

    `rename <char>` with no new name flags the rename for an ONLINE character
    (`Commands.cpp:12612-12623`); for an offline one the same spelling runs
    `UPDATE characters SET name = guid` (`:12624-12635`), throwing the name away
    and putting the numeric guid there. A button labelled "Rename at next login"
    that silently does that is data loss, so the entry carries a refusal and the
    tab draws it -- and, like the revive rule, it is the ENTRY's fact: WotLK,
    whose entry carries no such refusal, keeps its offline rename.
    """
    view = _view(tmp_path, TORTOISE, play=_Play(characters=_people(), cap=1))
    view.refresh_characters()

    view.character_list.setCurrentRow(0)
    assert view.rename_button.isEnabled() is True, "an online character can be flagged"
    assert view.rename_button.toolTip() == "", "nothing to explain about a character who is in"

    view.character_list.setCurrentRow(1)
    assert view.rename_button.isEnabled() is False
    said = view.rename_button.text()
    assert said.startswith("Ganaar "), said
    assert "logged in" in said, said
    # In two lengths, like the Ambiguous refusal twenty lines below it in the
    # view, and for the same reason: 8.4c put a 180-character sentence on one of
    # these labels and it ran off the end of the window
    # (`.notes/gates/8.4c-vanilla-m910q-2026-09-07/4-two-of-one-name.png`). This
    # refusal is ~200 characters. The bound is the one that box's own test
    # already uses, so a label that overflows fails here rather than in a
    # screenshot somebody takes later.
    assert len(said) < 60, f"{len(said)} characters on a button: {said}"
    hover = view.rename_button.toolTip()
    assert "guid" in hover, "the whole sentence says what the server would have done instead"
    assert hover == TORTOISE.play.rename_offline_refusal, hover

    # And the label goes back when a character who IS logged in is chosen again:
    # a stale refusal on a button that now works is the same defect the other
    # way round.
    view.character_list.setCurrentRow(0)
    assert view.rename_button.isEnabled() is True
    assert view.rename_button.text() == "Rename at next login Guglu"
    assert view.rename_button.toolTip() == ""

    wotlk = _view(tmp_path, WOTLK, play=_Play(characters=_people()))
    wotlk.refresh_characters()
    wotlk.character_list.setCurrentRow(1)
    assert wotlk.rename_button.isEnabled() is True, wotlk.rename_button.text()
    assert wotlk.rename_button.toolTip() == ""


def test_the_control_follows_the_measurement_rather_than_the_game_it_belongs_to(
    tmp_path: Path,
) -> None:
    """The two entries with their facts SWAPPED, which is the only way to prove
    the tab is not reading the id.

    A walk over the shipped catalog cannot catch it: an
    `if entry.id == "wow-tortoise"` in the view draws exactly what the walk
    expects, today, and is wrong about the fifth game and about this one the day
    the owner rebuilds the fork with its 2026-09-07 SOAP commit and whatever
    else that pull brings. So here Tortoise's entry is given a level command and
    WotLK's has its taken away, and the tab has to change its mind about both.
    """
    tortoise_with_one = TORTOISE.model_copy(
        update={
            "play": TORTOISE.play.model_copy(
                update={"set_level_command": "character level", "set_level_absent_reason": None}
            )
        }
    )
    wotlk_without_one = WOTLK.model_copy(
        update={
            "play": WOTLK.play.model_copy(
                update={
                    "set_level_command": None,
                    "set_level_absent_reason": "a sentence measured on some other server",
                }
            )
        }
    )

    gained = _view(tmp_path, tortoise_with_one, play=_Play(characters=_people(), cap=1))
    lost = _view(tmp_path, wotlk_without_one, play=_Play(characters=_people()))

    assert _drawn(gained.set_level_button) is True, "the id is Tortoise and the fact is not"
    assert _in_the_layout(gained.set_level_absent) is False
    assert _shown(gained.set_level_absent) is False
    assert _in_the_layout(lost.set_level_button) is False, "the id is WotLK and the fact is not"
    assert _shown(lost.set_level_button) is False, "the id is WotLK and the fact is not"
    assert lost.set_level_absent.text() == "a sentence measured on some other server"


# -- T96: choosing a character asks the database off the GUI thread ----------


def _threaded_after_the_list_is_filled(view: ControllerView) -> ThreadedJobRunner:
    """Swap the app's real runner in once the list is on screen, and hand it back.

    Filled inline first so this test is about the SELECTION's read and nothing
    else: the list's own refresh has its own test (T97).
    """
    runner = threaded_job_runner(view)
    view._jobs = runner
    return runner


class _Delivered(QObject):
    """A queued call posted to this thread AFTER a job's answer, to know it has been handled.

    `ThreadedJobRunner.wait()` returns once the worker has emitted its answer
    (queued onto this thread) and exited. A call queued now lands behind that
    answer, so when it runs, the answer has run too -- asked through Qt's own
    ordering, not through the runner's bookkeeping.
    """

    def __init__(self) -> None:
        super().__init__()
        self.reached = False

    @Slot()
    def mark(self) -> None:
        self.reached = True


def _every_answer_handled(runner: ThreadedJobRunner) -> None:
    assert runner.wait(HANG_BOUND_MS) is True
    flag = _Delivered()
    QMetaObject.invokeMethod(flag, "mark", Qt.ConnectionType.QueuedConnection)
    pump_until(lambda: flag.reached, "every answer the runner produced was handled")


def _drawn_on(view: ControllerView) -> list[int]:
    """The thread of every `setToolTip` on the gear button -- `_gear_read` draws through it."""
    threads: list[int] = []
    tooltip = view.send_gear_button.setToolTip

    def recording(text: str) -> None:
        threads.append(threading.get_ident())
        tooltip(text)

    view.send_gear_button.setToolTip = recording  # type: ignore[method-assign]
    return threads


def test_choosing_a_character_reads_the_gear_off_the_gui_thread(tmp_path: Path) -> None:
    """T96. Measured on yulon-win11, 2026-09-23 (Docker Desktop, WotLK, 900 characters):
    choosing a row ran `gear_set_size` ON THE GUI THREAD -- two `docker exec ...
    mysql` calls, ~240 ms each on that box -- so every arrow key through the list
    froze the window for about half a second, and so did every list refresh that
    kept a row selected (three of them after each Characters action). py-spy
    --idle put 81 GUI-thread samples inside it for ten key presses.
    """
    play = _Play(characters=_people(), pieces=19)
    asked_on: list[int] = []
    sized = play.gear_set_size

    def recording(character: str) -> tuple[int, int]:
        asked_on.append(threading.get_ident())
        return sized(character)

    play.gear_set_size = recording  # type: ignore[method-assign]
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    runner = _threaded_after_the_list_is_filled(view)
    drawn_on = _drawn_on(view)

    view.character_list.setCurrentRow(0)
    _every_answer_handled(runner)

    assert "19" in view.send_gear_button.text(), view.send_gear_button.text()
    assert view.send_gear_button.isEnabled() is True
    assert asked_on and threading.get_ident() not in asked_on, "the gear was read on the GUI thread"
    assert drawn_on and set(drawn_on) == {threading.get_ident()}, "the answer was drawn off it"


def _texts_set_on(view: ControllerView) -> list[str]:
    """Every label the gear button is given, in order."""
    texts: list[str] = []
    set_text = view.send_gear_button.setText

    def recording(text: str) -> None:
        texts.append(text)
        set_text(text)

    view.send_gear_button.setText = recording  # type: ignore[method-assign]
    return texts


class _CountingPlay(_Play):
    """Counts gear reads, the most ever running at once, and holds the first one on a gate."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.gate = threading.Event()
        self.started: list[str] = []
        self.running = 0
        self.most_at_once = 0
        self._lock = threading.Lock()

    def gear_set_size(self, character: str) -> tuple[int, int]:
        with self._lock:
            self.started.append(character)
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
            first = len(self.started) == 1
        try:
            if first:
                self.gate.wait(HANG_BOUND_MS / 1000)
            return super().gear_set_size(character)
        finally:
            with self._lock:
                self.running -= 1


def _roster(count: int) -> tuple[Character, ...]:
    return tuple(Character(i, f"Bot{i:02d}", 10, True, "RNDBOT") for i in range(1, count + 1))


def test_the_gear_button_waits_for_its_read_and_a_late_answer_for_another_row_is_dropped(
    tmp_path: Path,
) -> None:
    """While the read is out, the button promises nothing and cannot be pressed;
    an answer about a row that is no longer chosen never relabels it; and the row
    chosen while that read was out gets its own read when it lands."""
    play = _CountingPlay(characters=_people(), pieces=19)
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    runner = _threaded_after_the_list_is_filled(view)
    texts = _texts_set_on(view)

    view.character_list.setCurrentRow(0)  # Guglu, whose read is held
    assert view.send_gear_button.isEnabled() is False, "pressable before its count was known"
    assert "Guglu" in view.send_gear_button.text()

    view.character_list.setCurrentRow(1)  # Ganaar, while Guglu's read is still out
    assert "Ganaar" in view.send_gear_button.text()
    play.gate.set()
    pump_until(lambda: "Ganaar's 19" in view.send_gear_button.text(), "Ganaar's count was drawn")
    _every_answer_handled(runner)

    assert play.started == ["Guglu", "Ganaar"]
    assert not [t for t in texts if "Guglu's" in t], texts
    assert view.send_gear_button.isEnabled() is True


def test_a_burst_of_row_changes_runs_one_read_at_a_time_and_only_the_last_one_after(
    tmp_path: Path,
) -> None:
    """Codex, T96 review: every row change started its own worker and its own two
    `docker exec`s, so holding an arrow key down a 900-row roster queued hundreds
    of them, all running, all to be thrown away -- and a shutdown then waited on
    every one. One read at a time; the newest row waiting replaces any older one."""
    roster = _roster(51)
    play = _CountingPlay(characters=roster, pieces=19)
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    runner = _threaded_after_the_list_is_filled(view)

    for row in range(51):  # the first starts a read; fifty more arrive while it is out
        view.character_list.setCurrentRow(row)
    play.gate.set()
    last = roster[-1].name
    pump_until(lambda: f"{last}'s 19" in view.send_gear_button.text(), "the last row was drawn")
    _every_answer_handled(runner)

    assert play.most_at_once == 1, play.most_at_once
    assert play.started == [roster[0].name, last], play.started
    assert f"{last}'s 19" in view.send_gear_button.text()


def test_closing_the_tab_mid_read_starts_nothing_after_it(tmp_path: Path) -> None:
    """A row waiting behind a read still out when the tab closes is never read."""
    play = _CountingPlay(characters=_people(), pieces=19)
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    runner = _threaded_after_the_list_is_filled(view)

    view.character_list.setCurrentRow(0)  # Guglu, held
    view.character_list.setCurrentRow(1)  # Ganaar, waiting behind it
    release = threading.Timer(0.2, play.gate.set)
    release.start()
    view.shutdown()  # joins the held read once the timer lets it go
    release.join()
    _every_answer_handled(runner)

    assert play.started == ["Guglu"], play.started


def test_a_gear_read_that_breaks_says_so_rather_than_reading_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_gear_set_size()` answers its own failures, so this is the boundary for one
    it does not: the button must not stay on "Reading..." for the row still chosen,
    and a break about a row already left must not relabel the one chosen now."""
    play = _Play(characters=_people(), pieces=19)
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    runner = _threaded_after_the_list_is_filled(view)
    texts = _texts_set_on(view)
    gate = threading.Event()
    sizing = view._gear_set_size

    def breaks(seam: object, name: str) -> tuple[int, int, tuple[str, str] | None]:
        if name == "Guglu":
            gate.wait(HANG_BOUND_MS / 1000)
            raise RuntimeError("the worker fell over")
        return sizing(seam, name)

    monkeypatch.setattr(view, "_gear_set_size", breaks)

    view.character_list.setCurrentRow(0)  # Guglu, whose read will break late
    view.character_list.setCurrentRow(1)  # Ganaar, waiting behind it
    gate.set()
    pump_until(lambda: "Ganaar's 19" in view.send_gear_button.text(), "Ganaar's count was drawn")
    _every_answer_handled(runner)
    assert not [t for t in texts if t.startswith("Could not read")], "a stale break relabelled it"

    view.character_list.setCurrentRow(0)  # Guglu again, and now it breaks for the row chosen
    _every_answer_handled(runner)

    said = view.send_gear_button.text()
    assert said == "Could not read what Guglu is wearing", said
    assert view.send_gear_button.isEnabled() is False
    assert "the worker fell over" in view.send_gear_button.toolTip()


def test_the_gear_read_does_not_reach_back_into_the_view_from_its_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read runs on a worker, possibly after the view has started coming apart.

    Seen in the suite's own log (T96 review round): pytest tearing a view down
    changed the list's current row, `_character_chosen` dispatched a read, and
    the worker then found `'ControllerView' object has no attribute 'services'`.
    Harmless there -- the answer went nowhere -- but the worker has no business
    reading the view at all: what it needs is taken on the GUI thread when the
    row is chosen. A runner that holds the work lets the view lose its services
    first, deterministically.

    The services are taken away through `monkeypatch`, which puts them back at
    teardown (T131). A bare `del` left this view without them for good, and a
    timer it had already queued (`_fill_next_version`) then fired inside a LATER
    test's event pump and failed that test instead.
    """
    play = _Play(characters=_people(), pieces=19)
    view = _view(tmp_path, play=play)
    view.refresh_characters()
    held: list[tuple[object, object, object]] = []
    view._jobs = lambda work, on_done, on_error: held.append((work, on_done, on_error))

    view.character_list.setCurrentRow(1)  # Ganaar
    # What a view being torn down looks like to a late worker.
    monkeypatch.delattr(view, "services")

    work = held[-1][0]
    generation, name, (pieces, mails, refusal) = work()  # type: ignore[operator, misc]
    assert (name, pieces, mails, refusal) == ("Ganaar", 19, 2, None)
