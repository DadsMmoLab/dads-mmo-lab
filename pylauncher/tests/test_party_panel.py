"""Tests for `PartyPanel` (8.6): My Party's own surface, offscreen, through a stub seam.

Every press here goes through the SAME `MyPartySeam` the gate script drove
(`.notes/gates/8.6-wotlk-yulon-ubuntu2-2026-09-09/gate86b.py`), so what the panel
can do is exactly what was proved live on `yulon-ubuntu2`: read the state, add a
bot of a chosen class, see it in the party, dismiss it.

The stub answers with `party`'s own dataclasses rather than with look-alikes: the
four states of an `Addition` are the reason that class exists, and a fake with a
`joined` field of its own would let the panel treat "the server took the command"
as "a bot arrived", which is what "My Party works" used to mean (2026-08-20).
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from yulon import party
from yulon.ui.widgets.job import run_inline
from yulon.ui.widgets.party_panel import (
    DISMISS_ALL_CANCELLED,
    DISMISS_ALL_IDLE,
    DISMISS_ALL_MOVED,
    LEVEL_AS_MADE,
    LINK_IDLE,
    NO_ACCOUNT_TYPED,
    NO_BOTS_TO_DISMISS,
    PRESS_SHOW_CANDIDATES_FIRST,
    PRESS_SHOW_FIRST,
    SPEC_AUTO,
    WORKING,
    PartyPanel,
)

READY_CHECKS = (
    party.Precondition("server_installed", True, ""),
    party.Precondition("bridge_answered", True, ""),
)
"""Two of the nine, which is all any assertion here needs. `party.preconditions()`
owns the list and `test_party.py` owns its contents."""


class _StubParty:
    """Stands in for `party.InstallParty` — one read and two presses, recorded.

    A stub rather than the real object for the reason every other seam in these
    tests is stubbed: the real one reads a database and sends over a channel, and
    a panel test that needed either would be a test of a server.
    """

    def __init__(
        self,
        state: party.PartyState | None = None,
        addition: party.Addition | None = None,
        dismissal: party.Dismissal | None = None,
        mass: party.MassDismissal | None = None,
        specs: dict[str, tuple[str, ...]] | None = None,
        max_level: int | None = 80,
        picker: party.Picker | None = None,
        named: party.NamedAddition | None = None,
        plan: party.AccountLink | None = None,
        link: party.AccountLink | None = None,
    ) -> None:
        self.state_result = state or party.PartyState(True, "", READY_CHECKS)
        self.addition = addition or party.Addition(
            True, True, "Jilsur", True, True, "Jilsur joined the party, geared and specced."
        )
        self.dismissal = dismissal or party.Dismissal(True, True, "Jilsur left the party.")
        self.mass = mass or party.MassDismissal(0, (), "nothing to do")
        self.spec_lists = {} if specs is None else specs
        self.top_level = max_level
        self.asked: list[str] = []
        self.added: list[tuple[str, str, str, int | None]] = []
        self.removed: list[tuple[str, str]] = []
        self.dismissed_all: list[tuple[str, tuple[int, ...]]] = []
        self.dismissed: list[str] = []
        # T26. The picker, the named add and the two halves of the link, each
        # recorded the same way: what the panel asked for is the assertion.
        self.picker = picker or party.Picker()
        self.named = named or party.NamedAddition(True, True, "Nore", "Nore joined the party.")
        self.plan = plan or party.AccountLink(False, "PERZI", "FRIEND", "would link them")
        self.link = link or party.AccountLink(
            True, "PERZI", "FRIEND", "PERZI and FRIEND are linked."
        )
        self.picked: list[str] = []
        self.added_named: list[tuple[str, str]] = []
        self.planned: list[tuple[str, str]] = []
        self.linked: list[tuple[str, str]] = []

    def state(self, master: str) -> party.PartyState:
        self.asked.append(master)
        return self.state_result

    def specs(self, klass: str) -> tuple[str, ...]:
        return self.spec_lists.get(klass, ())

    def max_level(self) -> int | None:
        return self.top_level

    def add(
        self,
        master: str,
        klass: str,
        *,
        gender: str = "",
        spec: str = "",
        level: int | None = None,
    ) -> party.Addition:
        self.added.append((master, klass, spec, level))
        return self.addition

    def candidates(self, master: str) -> party.Picker:
        self.picked.append(master)
        return self.picker

    def add_named(self, master: str, name: str) -> party.NamedAddition:
        self.added_named.append((master, name))
        return self.named

    def link_plan(self, master: str, account: str) -> party.AccountLink:
        self.planned.append((master, account))
        return self.plan

    def link_account(self, master: str, account: str) -> party.AccountLink:
        self.linked.append((master, account))
        return self.link

    def remove(self, master: str, bot: str) -> party.Dismissal:
        self.removed.append((master, bot))
        return self.dismissal

    def remove_all(self, master: str, confirmed: tuple[int, ...]) -> party.MassDismissal:
        """The real seam's contract, honoured rather than recorded.

        A stub that took the guids and dismissed regardless would let every test
        below pass over a panel that sent the wrong ones: what the confirmation
        buys is that the SEAM re-reads and refuses, so the stub re-reads its own
        `state_result` and refuses too. The real refusal's wording is
        `test_party.py`'s subject; what is proved here is that the panel passes
        the identities it confirmed and shows what came back.
        """
        self.dismissed_all.append((master, confirmed))
        rows = self.state_result.members
        if {row.guid for row in rows} != set(confirmed):
            moved = "the party is not the one that was confirmed. Nothing was sent."
            return party.MassDismissal(0, (), moved, blocker=moved)
        self.dismissed.extend(row.name for row in rows)
        # The party is empty afterwards, because that is what dismissing every
        # bot does. Poking `state_result` by hand in a test instead would let
        # the confirmation check see a party the dismissal never happened to.
        self.state_result = party.PartyState(True, "", READY_CHECKS)
        return self.mass


def _member(name: str = "Jilsur", guid: int = 948) -> party.Member:
    return party.Member(name=name, guid=guid, klass=8, level=1)


def _panel(stub: _StubParty) -> PartyPanel:
    return PartyPanel(stub, jobs=run_inline)


def _rows(panel: PartyPanel) -> list[str]:
    return [panel.member_list.item(i).text() for i in range(panel.member_list.count())]


def _checks(panel: PartyPanel) -> list[str]:
    return [panel.check_list.item(i).text() for i in range(panel.check_list.count())]


def test_the_party_shows_the_bots_the_seam_reports(qapp: object) -> None:
    """The rows, named, with what the group table says about each."""
    stub = _StubParty(
        state=party.PartyState(True, "", READY_CHECKS, members=(_member(), _member("Anmi", 949)))
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.refresh_party()

    assert stub.asked == ["Pakka"]
    assert any("Jilsur" in row for row in _rows(panel))
    assert any("Anmi" in row for row in _rows(panel))


def test_a_blocker_is_the_whole_answer_and_no_row_is_drawn(qapp: object) -> None:
    """The precondition that stopped it, and never an empty list beside it.

    `PartyState`'s own docstring is the reason: the empty list a broken bridge
    produces is the same empty list a working party with no bots in it produces,
    and the 2026-08-20 failure is that pair being told apart wrongly.
    """
    stopped = (
        party.Precondition("engine_enabled", True, ""),
        party.Precondition("script_path", False, "the Lua engine is looking for scripts in x"),
    )
    stub = _StubParty(
        state=party.PartyState(False, "the Lua engine is looking for scripts in x", stopped)
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.refresh_party()

    assert _rows(panel) == []
    assert "the Lua engine is looking for scripts in x" in panel.summary.text()
    assert _checks(panel) == ["ok engine_enabled", "not yet: script_path"]
    assert panel.report.text() == "", "a refused read must not leave `Working…` standing"


def test_the_unmet_check_carries_its_own_sentence(qapp: object) -> None:
    """All nine are listed, and the one that failed says why where a person can read it.

    `PartyState.checks` carries every check rather than only the blocking one so
    somebody fixing this can see how far down the list they have got.
    """
    stopped = (
        party.Precondition("engine_in_binary", False, "the Lua engine is not in the binary"),
    )
    stub = _StubParty(state=party.PartyState(False, "the Lua engine is not in the binary", stopped))
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.refresh_party()

    assert panel.check_list.item(0).toolTip() == "the Lua engine is not in the binary"


def test_a_party_that_could_not_be_read_says_the_problem_and_shows_no_rows(qapp: object) -> None:
    """`ready` with a `problem` is the third answer: the checks passed, the READ did not."""
    stub = _StubParty(
        state=party.PartyState(True, "", READY_CHECKS, problem="Pakka is not logged in.")
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.refresh_party()

    assert _rows(panel) == []
    assert "not logged in" in panel.summary.text()


def test_a_refusal_does_not_leave_the_last_readings_rows_on_screen(qapp: object) -> None:
    """The ground is read first: the rows ARE there before the refusal."""
    stub = _StubParty(state=party.PartyState(True, "", READY_CHECKS, members=(_member(),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    assert _rows(panel) != [], "the ground: a party was on screen before the refusal"

    stub.state_result = party.PartyState(False, "the world server is not running", READY_CHECKS)
    panel.refresh_party()

    assert _rows(panel) == []
    assert "the world server is not running" in panel.summary.text()


def test_no_character_named_sends_nothing_at_all(qapp: object) -> None:
    """Neither the read nor either press reaches the seam with an empty box."""
    stub = _StubParty()
    panel = _panel(stub)

    panel.refresh_party()
    panel.add_bot()
    panel.dismiss_bot()

    assert (stub.asked, stub.added, stub.removed) == ([], [], [])
    assert "Type the name of the character" in panel.report.text()


def test_the_classes_offered_are_the_ones_this_trees_addclass_takes(qapp: object) -> None:
    """Read off `party.BOT_CLASSES`, so a class this tree refuses cannot be offered.

    `dk` is in that tuple because THIS tree's `addclass` takes it, which the bash
    launcher's own list deliberately did not -- a per-tree fact, measured per
    tree. The panel offering its own list is how the two drift.
    """
    panel = _panel(_StubParty())

    offered = [panel.klass.itemText(i) for i in range(panel.klass.count())]

    assert tuple(offered) == party.BOT_CLASSES


def test_the_press_sends_the_chosen_class_and_the_servers_own_sentence_survives_the_re_read(
    qapp: object,
) -> None:
    """The add's own report text is still on screen after the group is re-read.

    Two things happen on one press: the seam says what it did, and the party is
    read again -- because the panel must never draw a row the group table has not
    got. The second must not silently replace the first. A panel that ends a
    press reading "1 bot in this party" has thrown away the only sentence that
    said whether the bot was GEARED, or whether the server took the command and
    no bot ever arrived.
    """
    stub = _StubParty(state=party.PartyState(True, "", READY_CHECKS, members=(_member(),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.klass.setCurrentText("mage")

    panel.add_bot()

    assert stub.added == [("Pakka", "mage", "", None)]
    assert panel.report.text() == "Jilsur joined the party, geared and specced."
    assert any("Jilsur" in row for row in _rows(panel)), "the group was re-read, not assumed"


def test_a_bot_that_never_joined_is_not_drawn_as_a_party_member(qapp: object) -> None:
    """The third outcome, and the panel adds no row of its own for it.

    `Addition(added=True, joined=False)` is the server taking the command and no
    bot arriving within the poll window. Reporting that as success is what
    "My Party works" used to mean, and a panel that drew its own row after a
    `yes` would be saying it in a different place.
    """
    stub = _StubParty(
        addition=party.Addition(
            True,
            False,
            None,
            False,
            False,
            "the server accepted the command and no bot joined the party within 6 seconds.",
        )
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.add_bot()

    assert _rows(panel) == []
    assert "no bot joined the party" in panel.report.text()


def test_a_press_stopped_by_a_precondition_says_which_one_and_sends_nothing(qapp: object) -> None:
    """`blocker` set is the seam saying it never touched the channel."""
    stopped = "the Lua engine is switched off: ALE.Enabled is not set to 1 in mod_ale.conf."
    stub = _StubParty(
        addition=party.Addition(False, False, None, False, False, stopped, blocker=stopped),
        state=party.PartyState(False, stopped, READY_CHECKS),
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.add_bot()

    assert "ALE.Enabled" in panel.report.text()
    assert _rows(panel) == []


def test_the_dismiss_button_names_the_bot_it_would_send_away(qapp: object) -> None:
    """The Characters tab's rule: a button somebody presses says what it acts on."""
    stub = _StubParty(state=party.PartyState(True, "", READY_CHECKS, members=(_member(),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    assert panel.dismiss_button.text() == "Dismiss", "the ground: nothing is chosen yet"
    assert panel.dismiss_button.isEnabled() is False

    panel.member_list.setCurrentRow(0)

    assert panel.dismiss_button.text() == "Dismiss Jilsur"
    assert panel.dismiss_button.isEnabled() is True


def test_dismiss_with_no_bot_chosen_sends_nothing(qapp: object) -> None:
    stub = _StubParty(state=party.PartyState(True, "", READY_CHECKS, members=(_member(),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()

    panel.dismiss_bot()

    assert stub.removed == []
    assert panel.report.text() == "Choose a bot in the party list first. Nothing was sent."


def test_dismissing_reads_the_group_back_rather_than_dropping_the_row_itself(
    qapp: object,
) -> None:
    """`Dismissal.removed` is the group table read afterwards; so is the list."""
    stub = _StubParty(state=party.PartyState(True, "", READY_CHECKS, members=(_member(),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    panel.member_list.setCurrentRow(0)
    assert _rows(panel) != [], "the ground: the bot is in the list before the press"
    stub.state_result = party.PartyState(True, "", READY_CHECKS)

    panel.dismiss_bot()

    assert stub.removed == [("Pakka", "Jilsur")]
    assert panel.report.text() == "Jilsur left the party."
    assert _rows(panel) == []


def test_a_second_press_is_refused_while_one_is_in_flight(qapp: object) -> None:
    """`add_bot` polls for up to six seconds; a person will press again.

    Two adds in flight would poll each other's bots -- `party.add_bot` decides a
    bot arrived by finding a guid that was not in ITS own before-reading.
    """
    held: list[object] = []

    def never_finishes(work: object, on_done: object, on_error: object) -> None:
        held.append(work)

    stub = _StubParty()
    panel = PartyPanel(stub, jobs=never_finishes)  # type: ignore[arg-type]
    panel.character.setText("Pakka")
    # The two reads the panel starts with -- this install's spec list and its
    # top level -- are jobs too, and this one's runner never finishes any of
    # them. What is being counted here is presses, so the constructor's reads
    # are dropped rather than left to make the assertion below say 3.
    held.clear()

    panel.add_bot()
    assert panel.add_button.isEnabled() is False, "the ground: the button is locked while it runs"
    panel.add_bot()
    panel.refresh_party()

    assert len(held) == 1


def test_a_seam_that_raises_becomes_a_sentence_and_the_panel_stays_usable(qapp: object) -> None:
    """Every seam failure has one answer, and the panel is pressable afterwards."""

    class _Broken(_StubParty):
        def state(self, master: str) -> party.PartyState:
            raise RuntimeError("no such container")

    panel = _panel(_Broken())
    panel.character.setText("Pakka")

    panel.refresh_party()

    assert "no such container" in panel.report.text()
    assert panel.add_button.isEnabled() is True


def test_a_press_that_reached_the_server_says_so_and_a_local_refusal_does_not(
    qapp: object,
) -> None:
    """`party_changed` is for whoever else is counting bots (design :111)."""
    stub = _StubParty()
    panel = _panel(stub)
    heard: list[int] = []
    panel.party_changed.connect(lambda: heard.append(1))

    panel.add_bot()  # no character named: refused here, nothing sent
    assert heard == [], "the ground: a local refusal reached no server"

    panel.character.setText("Pakka")
    panel.add_bot()

    assert heard == [1]


# -- 8.6, T5: the chosen spec, the chosen level, dismiss all ----------------


def _offered(panel: PartyPanel) -> list[str]:
    return [panel.spec.itemText(i) for i in range(panel.spec.count())]


def test_the_specs_offered_are_the_ones_this_install_lists_for_the_chosen_class(
    qapp: object,
) -> None:
    """The picker is the conf's list for the class in the box beside it.

    Two reasons it is filtered by class rather than showing all of them: the
    module compares the whispered name against `premadeSpecName[<the BOT's
    class>]` only (`ChangeTalentsAction.cpp:144`), so a mage offered `arms pve`
    is a mage offered a name that can only fail; and the failure is invisible —
    `Spec <x> not found` goes to the game window and never to this app.
    """
    stub = _StubParty(specs={"mage": ("arcane pve", "fire pve"), "druid": ("balance pve",)})
    panel = _panel(stub)

    panel.klass.setCurrentText("mage")

    assert _offered(panel) == [SPEC_AUTO, "arcane pve", "fire pve"]
    panel.klass.setCurrentText("druid")
    assert _offered(panel) == [SPEC_AUTO, "balance pve"]


def test_a_class_this_install_lists_no_specs_for_still_offers_the_servers_own_pick(
    qapp: object,
) -> None:
    """An empty picker would be a control a person cannot use and cannot explain.
    Autopick is what `add()` has always done and it is still there."""
    panel = _panel(_StubParty(specs={}))

    assert _offered(panel) == [SPEC_AUTO]


def test_the_chosen_spec_is_what_the_press_carries_and_the_servers_own_pick_carries_nothing(
    qapp: object,
) -> None:
    """`spec=""` is the seam's word for "let the server pick", which is the
    `talents autopick` whisper `add_bot` has always sent."""
    stub = _StubParty(specs={"mage": ("arcane pve", "fire pve")})
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.klass.setCurrentText("mage")

    panel.add_bot()
    assert stub.added[-1] == ("Pakka", "mage", "", None), "the ground: nothing chosen"

    panel.spec.setCurrentText("fire pve")
    panel.add_bot()

    assert stub.added[-1] == ("Pakka", "mage", "fire pve", None)


def test_the_level_box_is_bounded_by_this_servers_own_maximum(qapp: object) -> None:
    """80 is not in this panel. The bound is `MaxPlayerLevel` off the install
    (measured 80 on `yulon-ubuntu2` 2026-09-09) and a fork that ships 60 bounds
    the same box at 60."""
    panel = _panel(_StubParty(max_level=60))

    assert panel.level.maximum() == 60
    assert panel.level.isEnabled() is True


def test_a_server_whose_maximum_could_not_be_read_offers_no_level_and_says_why(
    qapp: object,
) -> None:
    """The Characters tab's rule for a control this tree cannot have (8.4d): not a
    disabled button with no explanation and not an empty space, but the sentence
    naming what to fix. Here the fix is a conf key, so the sentence names it."""
    panel = _panel(_StubParty(max_level=None))

    assert panel.level.isEnabled() is False
    assert party.MAX_LEVEL_KEY in panel.level_absent.text()
    assert panel.level_absent.isVisibleTo(panel) is True


def test_a_level_left_alone_sends_no_level_at_all(qapp: object) -> None:
    """The box has to have a value and "no level" has to be one of them, so the
    bottom of the range is not level 1 -- it is the words "leave it alone".
    Sending level 1 for a box nobody touched would reset every bot to 1."""
    stub = _StubParty(max_level=80)
    panel = _panel(stub)
    panel.character.setText("Pakka")
    assert panel.level.value() == 0
    assert panel.level.text() == LEVEL_AS_MADE

    panel.add_bot()
    assert stub.added[-1][3] is None

    panel.level.setValue(60)
    panel.add_bot()

    assert stub.added[-1][3] == 60


def test_dismiss_all_arms_first_and_names_the_count_it_would_send_away(qapp: object) -> None:
    """The tab's own two-press gesture (`controller_view.REMOVE_ARMED`), for the
    same reason: a teardown should not be one click away. The armed label carries
    the count, so what the second press does is on the button itself."""
    stub = _StubParty(
        state=party.PartyState(True, "", READY_CHECKS, members=(_member(), _member("Anmi", 949)))
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    assert panel.dismiss_all_button.text() == DISMISS_ALL_IDLE

    panel.dismiss_all()

    assert stub.dismissed_all == [], "the first press arms and sends nothing"
    assert "2 bots" in panel.dismiss_all_button.text()
    assert "Pakka" in panel.report.text()


def test_the_second_press_sends_it_and_the_group_is_read_back(qapp: object) -> None:
    """The rows go because the group table no longer has them, never because the
    panel dropped them: `remove_all` reports per bot and the list is re-read."""
    stub = _StubParty(
        state=party.PartyState(True, "", READY_CHECKS, members=(_member(), _member("Anmi", 949))),
        mass=party.MassDismissal(
            2,
            (
                party.Dismissal(True, True, "Anmi left the party.", bot="Anmi"),
                party.Dismissal(True, True, "Jilsur left the party.", bot="Jilsur"),
            ),
            "2 bots left the party: Anmi, Jilsur.",
        ),
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    panel.dismiss_all()

    panel.dismiss_all()

    assert stub.dismissed_all == [("Pakka", (948, 949))]
    assert panel.report.text() == "2 bots left the party: Anmi, Jilsur."
    assert _rows(panel) == []
    assert panel.dismiss_all_button.text() == DISMISS_ALL_IDLE, "it disarms once it has fired"


def test_dismiss_all_with_no_bots_in_the_list_sends_nothing_and_never_arms(qapp: object) -> None:
    """Refused here rather than at the seam, for the same reason an empty
    character box is: it costs a database read and a `docker exec` to be told
    what the panel can already see, and arming a button over an empty party is
    asking somebody to confirm nothing."""
    stub = _StubParty()
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()

    panel.dismiss_all()
    panel.dismiss_all()

    assert stub.dismissed_all == []
    assert panel.dismiss_all_button.text() == DISMISS_ALL_IDLE
    assert "nothing to dismiss" in panel.report.text()


def test_showing_the_party_stands_an_armed_dismiss_all_down_and_says_so(qapp: object) -> None:
    """The idle button's own escape route, and the tab's (`REMOVE_ARMED`'s
    paragraph says "Press Refresh to cancel"). It has to SAY it happened: a read
    clears the report line, so an armed press followed by a refresh would
    otherwise leave a disarmed button and no record of the cancellation."""
    stub = _StubParty(state=party.PartyState(True, "", READY_CHECKS, members=(_member(),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    panel.dismiss_all()
    assert "1 bot" in panel.dismiss_all_button.text(), "the ground: it is armed"

    panel.refresh_party()

    assert panel.dismiss_all_button.text() == DISMISS_ALL_IDLE
    assert panel.report.text() == DISMISS_ALL_CANCELLED
    assert stub.dismissed_all == []


def test_an_add_stands_an_armed_dismiss_all_down_and_its_own_report_wins(qapp: object) -> None:
    """Any other action means the user moved on from this one
    (`controller_view._disarm_actions`), and the action they DID take owns the
    report line -- a cancellation notice over an add's own sentence would throw
    away the only thing saying whether the bot arrived."""
    stub = _StubParty(state=party.PartyState(True, "", READY_CHECKS, members=(_member(),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    panel.dismiss_all()

    panel.add_bot()

    assert stub.dismissed_all == []
    assert panel.dismiss_all_button.text() == DISMISS_ALL_IDLE
    assert panel.report.text() == "Jilsur joined the party, geared and specced."


# -- round 2: what an arm is ABOUT, and reads that land out of order --------


def _deferred() -> tuple[list[tuple[object, object, object]], object]:
    """A runner that queues jobs so a test can finish them in any order it likes."""
    pending: list[tuple[object, object, object]] = []

    def defer(work: object, on_done: object, on_error: object) -> None:
        pending.append((work, on_done, on_error))

    return pending, defer


def _armed_panel(stub: _StubParty) -> PartyPanel:
    """A panel showing Pakka's two bots, with dismiss-all armed on them."""
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    panel.dismiss_all()
    assert "2 bots" in panel.dismiss_all_button.text(), "the ground: armed on the two on screen"
    return panel


def _two_bots() -> party.PartyState:
    return party.PartyState(True, "", READY_CHECKS, members=(_member(), _member("Anmi", 949)))


def test_an_armed_dismiss_all_is_bound_to_the_character_it_named(qapp: object) -> None:
    """Round 1, both reviewers, and the sharpest edge on this panel.

    Armed on Pakka's two bots, a person who then types another name into the box
    and presses again used to send `remove_all("Anmi")` -- against a party that
    had never been drawn, counted, or named in the sentence they agreed to. Only
    Enter disarmed, and typing a name is not pressing Enter.
    """
    stub = _StubParty(state=_two_bots())
    panel = _armed_panel(stub)

    panel.character.setText("Anmi")
    assert panel.dismiss_all_button.text() == DISMISS_ALL_IDLE, "typing stands the arm down"

    panel.dismiss_all()

    assert stub.dismissed_all == []
    assert panel.dismiss_all_button.text() == DISMISS_ALL_IDLE
    assert panel.report.text() == DISMISS_ALL_MOVED


def test_the_arming_sentence_names_the_bots_the_second_press_would_send_away(
    qapp: object,
) -> None:
    """What was confirmed has to be readable, or the identity check guards a
    promise nobody was shown."""
    panel = _armed_panel(_StubParty(state=_two_bots()))

    assert "Jilsur" in panel.report.text()
    assert "Anmi" in panel.report.text()
    assert "Pakka" in panel.report.text()


def test_the_same_character_and_the_same_bots_still_fire_on_the_second_press(
    qapp: object,
) -> None:
    """The control for the two tests above: an unchanged subject is not a moved
    one, and the check must not have made the button unpressable."""
    stub = _StubParty(state=_two_bots())
    panel = _armed_panel(stub)

    panel.dismiss_all()

    assert stub.dismissed_all == [("Pakka", (948, 949))]


def test_a_spec_reading_that_lands_after_a_newer_one_is_dropped(qapp: object) -> None:
    """Round 1 (Codex): each class change starts an independent read.

    With two in flight the runner decides which finishes first, and a slower
    MAGE read landing after a DRUID read used to fill the druid picker with mage
    specs -- so the next Add would send a spec for the wrong class, which this
    tree's module answers `Spec <x> not found` to in the game window, where
    nothing this app can read will ever see it.
    """
    pending, defer = _deferred()
    stub = _StubParty(specs={"mage": ("fire pve",), "druid": ("balance pve",)})
    panel = PartyPanel(stub, jobs=defer)  # type: ignore[arg-type]
    pending.clear()  # the two readings the constructor starts

    panel.klass.setCurrentText("mage")
    panel.klass.setCurrentText("druid")
    assert len(pending) == 2, "the ground: two spec readings are in flight"
    for work, on_done, _fail in reversed(pending):  # druid first, then the slower mage
        on_done(work())  # type: ignore[operator]

    assert _offered(panel) == [SPEC_AUTO, "balance pve"]


def test_a_reading_that_raises_beside_a_press_does_not_unlock_the_press(qapp: object) -> None:
    """`specs()` and `max_level()` ride along beside a press; their failures must
    not re-arm the buttons under one.

    Round 1 flagged it as not blocking, and the cost if it happened is the one
    `_busy` exists to prevent: two adds in flight poll each other's bots, because
    `party.add_bot` decides a bot arrived by finding a guid that was not in ITS
    own before-reading.
    """
    pending, defer = _deferred()

    class _BadSpecs(_StubParty):
        def specs(self, klass: str) -> tuple[str, ...]:
            raise RuntimeError("no playerbots.conf here")

    stub = _BadSpecs()
    panel = PartyPanel(stub, jobs=defer)  # type: ignore[arg-type]
    pending.clear()
    panel.character.setText("Pakka")
    panel.add_bot()
    assert panel.add_button.isEnabled() is False, "the ground: a press is in flight"

    panel.klass.setCurrentText("mage")
    work, _done, on_error = pending[-1]
    try:
        work()  # type: ignore[operator]
    except RuntimeError as exc:
        on_error(exc)  # type: ignore[operator]

    assert panel.add_button.isEnabled() is False
    assert panel.report.text() == WORKING, "the press's own line is what a person is waiting for"


def test_the_pickers_are_locked_while_a_press_is_in_flight(qapp: object) -> None:
    """A class changed mid-press re-reads the spec list under an add that has
    already sent its class, and a spec or level chosen while the server is being
    asked is one the answer on screen will not be about."""
    pending, defer = _deferred()
    panel = PartyPanel(_StubParty(), jobs=defer)  # type: ignore[arg-type]
    panel.character.setText("Pakka")
    assert panel.klass.isEnabled() is True, "the ground: they are usable when nothing is running"

    panel.add_bot()

    assert (panel.klass.isEnabled(), panel.spec.isEnabled(), panel.level.isEnabled()) == (
        False,
        False,
        False,
    )


# -- round 3: the set that was confirmed is the set that is sent away -------


def test_a_party_that_moved_on_the_server_is_not_dismissed_unconfirmed(qapp: object) -> None:
    """Round 2 (Codex): the on-screen check guarded the wrong side of the door.

    The second press compared the DRAWN rows to the confirmed ones and then
    called a seam that read the party fresh and dismissed whatever it found. A
    bot that joined between the two presses -- a party filling up while somebody
    reads the sentence -- passed the on-screen check, because nothing on screen
    had changed, and went away unconfirmed. What binds the two halves is that
    the confirmed identities travel WITH the press: the seam re-reads, refuses
    unless the party is exactly the set that was agreed to, and dismisses only
    that snapshot.

    The identities are guids from the group read and not names parsed back out
    of the rows: a name is what a display shows, and a guid is what the group
    table has.
    """
    stub = _StubParty(state=_two_bots())
    panel = _armed_panel(stub)
    # The party moves on the SERVER. Nothing on screen changes, and nothing
    # re-reads it -- which is exactly the window the arm sits in.
    stub.state_result = party.PartyState(
        True, "", READY_CHECKS, members=(_member(), _member("Anmi", 949), _member("Kobo", 950))
    )

    panel.dismiss_all()

    assert stub.dismissed == [], "nothing may be sent away"
    assert stub.dismissed_all == [("Pakka", (948, 949))], "the guids confirmed went with the press"
    assert "confirmed" in panel.report.text()


def test_the_press_carries_the_guids_that_were_confirmed_and_not_the_names_on_screen(
    qapp: object,
) -> None:
    """The control for the test above: an unchanged party still fires, and what
    it fires with is the group table's own identities."""
    stub = _StubParty(state=_two_bots())
    panel = _armed_panel(stub)

    panel.dismiss_all()

    assert stub.dismissed_all == [("Pakka", (948, 949))]
    assert stub.dismissed == ["Jilsur", "Anmi"]


def test_a_party_drawn_for_another_character_cannot_be_armed(qapp: object) -> None:
    """Round 2 (Fable): a stale list under a new name armed happily.

    Show as Pakka, retype the box as Anmi without pressing Show, press Dismiss
    every bot: nothing was armed, so nothing stood down, and the arm took
    ("Anmi", Pakka's rows) -- a sentence naming Pakka's bots over a press
    addressed to Anmi's party. The rows now remember which character they were
    drawn for, and an arm is refused until the two agree.
    """
    stub = _StubParty(state=_two_bots())
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()

    panel.character.setText("Anmi")
    panel.dismiss_all()

    assert stub.dismissed_all == []
    assert panel.dismiss_all_button.text() == DISMISS_ALL_IDLE
    assert panel.report.text() == PRESS_SHOW_FIRST


def test_a_refused_read_leaves_no_party_to_arm_on(qapp: object) -> None:
    """The rows AND the name they were drawn for go together when a read fails.

    Otherwise a blocker clears the list and leaves `Pakka` behind it, and the
    next press arms on an empty confirmation -- which the seam would read as
    "confirm that this party is empty" and act on.
    """
    stub = _StubParty(state=_two_bots())
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.refresh_party()
    assert _rows(panel) != [], "the ground: a party was on screen"
    stub.state_result = party.PartyState(False, "the world server is not running", READY_CHECKS)
    panel.refresh_party()

    panel.dismiss_all()

    assert stub.dismissed_all == []
    assert panel.report.text() == NO_BOTS_TO_DISMISS


def test_a_level_bound_that_lands_during_a_press_does_not_hand_the_box_back(qapp: object) -> None:
    """The level reading the panel starts at birth can finish under the first press.

    A mutation survived here in round 3 and this test is why it does not any
    more: every earlier test either had no press in flight or never let that
    reading complete, so `setEnabled(True)` in the completion was never executed
    while it mattered. It matters because the reading and the press are
    independent -- the panel is built, somebody presses Add straight away, and
    the conf read lands a moment later on top of a locked panel.
    """
    pending, defer = _deferred()
    panel = PartyPanel(_StubParty(max_level=80), jobs=defer)  # type: ignore[arg-type]
    level_read = pending[0]  # `_load_level_bound` runs first in __init__
    panel.character.setText("Pakka")

    panel.add_bot()
    assert panel.level.isEnabled() is False, "the ground: the press locked it"
    work, on_done, _fail = level_read
    on_done(work())  # type: ignore[operator]

    assert panel.level.isEnabled() is False
    assert panel.level.maximum() == 80, "the bound still arrived; only the control stayed locked"


# -- T26: adding a named character, and linking an account -------------------


def _candidate(
    name: str = "Nore",
    *,
    allowed_by: str = party.ALLOWED_SAME_ACCOUNT,
    refused_because: str = "",
) -> party.Candidate:
    return party.Candidate(
        name=name,
        level=60,
        klass=8,
        account_or_guild="PERZI",
        allowed_by=allowed_by,
        refused_because=refused_because,
    )


def _candidates_shown(panel: PartyPanel) -> list[str]:
    """The picker's rows as they are drawn. NOT `_offered` above, which is the
    spec combo -- two lists on one panel and one name for both is how a test
    passes over the wrong widget."""
    return [panel.candidate_list.item(i).text() for i in range(panel.candidate_list.count())]


def test_add_this_character_sits_beside_add_a_bot_and_not_instead_of_it(qapp: object) -> None:
    """The owner asked for a second way in, not a different one. "Add a bot"
    makes a bot of a chosen class; this one puts an EXISTING character -- an
    alt, a guild mate, a friend's character -- into the party."""
    panel = _panel(_StubParty())
    assert panel.add_button.text() == "Add a bot"
    assert panel.add_named_button.isEnabled() is False, "nothing is chosen yet"
    assert panel.candidates_button.text()


def test_the_picker_draws_every_row_and_greys_the_ones_the_server_would_refuse(
    qapp: object,
) -> None:
    """A refused row is SHOWN with its reason rather than left out: "why is my
    alt not in this list" is the question a filtered list cannot answer, and the
    module's own refusal goes to the game window where nothing can hear it.

    Mutation: draw only the allowed rows. The list then reads as the whole truth
    about a server where 500 characters were refused for being logged in.
    """
    stub = _StubParty(
        picker=party.Picker(
            rows=(
                _candidate("Nore"),
                _candidate("Busy", allowed_by="", refused_because=party.REFUSED_ONLINE),
            )
        )
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.show_candidates()

    assert stub.picked == ["Pakka"]
    assert any("Nore" in row for row in _candidates_shown(panel))
    assert any(party.REFUSED_ONLINE in row for row in _candidates_shown(panel))
    assert panel.candidate_list.item(0).flags() & Qt.ItemFlag.ItemIsEnabled
    assert not panel.candidate_list.item(1).flags() & Qt.ItemFlag.ItemIsEnabled


def test_pressing_add_this_character_reaches_the_seam_with_the_chosen_name(
    qapp: object,
) -> None:
    """The name the seam is handed is the row a person chose, and the report is
    the seam's own sentence."""
    stub = _StubParty(picker=party.Picker(rows=(_candidate("Nore"),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.show_candidates()
    panel.candidate_list.setCurrentRow(0)

    panel.add_named()

    assert stub.added_named == [("Pakka", "Nore")]
    assert panel.report.text() == "Nore joined the party."


def test_a_row_read_for_another_character_cannot_be_pressed(qapp: object) -> None:
    """The rows and the character they were read FOR travel together, `_drawn`'s
    rule: a list read as Pakka and pressed as Anmi would send Anmi a name
    nothing had worked out the rules for."""
    stub = _StubParty(picker=party.Picker(rows=(_candidate("Nore"),)))
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.show_candidates()
    panel.candidate_list.setCurrentRow(0)
    panel.character.setText("Anmi")

    panel.add_named()

    assert stub.added_named == []
    assert panel.report.text() == PRESS_SHOW_CANDIDATES_FIRST


def test_a_picker_that_could_not_be_read_draws_no_rows_and_says_why(qapp: object) -> None:
    """`Picker.problem`'s reason, on screen: a short list and a failed read look
    identical."""
    stub = _StubParty(picker=party.Picker(problem="could not read which characters could be added"))
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.show_candidates()

    assert _candidates_shown(panel) == []
    assert "could not read" in panel.candidate_note.text()


def test_linking_an_account_asks_first_and_names_both_accounts(qapp: object) -> None:
    """One press arms and says what it would do; the second writes. The panel's
    own gesture (`DISMISS_ALL_IDLE`), not a modal -- the answers here arrive
    from a worker thread."""
    stub = _StubParty(
        plan=party.AccountLink(False, "PERZI", "FRIEND", "This links the account PERZI to FRIEND")
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.link_name.setText("FRIEND")

    panel.link_account()

    assert stub.planned == [("Pakka", "FRIEND")]
    assert stub.linked == [], "the first press writes nothing"
    assert "PERZI" in panel.report.text() and "FRIEND" in panel.report.text()

    panel.link_account()

    assert stub.linked == [("Pakka", "FRIEND")]
    assert panel.report.text() == "PERZI and FRIEND are linked."
    assert panel.link_button.text() == LINK_IDLE


def test_a_link_the_seam_refuses_never_arms(qapp: object) -> None:
    """A refusal is the whole answer: an unknown account, the master's own, or a
    pair already linked. Arming over one would ask somebody to confirm a write
    that cannot happen."""
    stub = _StubParty(
        plan=party.AccountLink(
            False, "PERZI", "", "there is no account called NOBODY", blocker="no such account"
        )
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.link_name.setText("NOBODY")

    panel.link_account()
    panel.link_account()

    assert stub.linked == []
    assert stub.planned == [
        ("Pakka", "NOBODY"),
        ("Pakka", "NOBODY"),
    ], "each press asks again -- an account can be made between two presses"
    assert "no account called NOBODY" in panel.report.text()


def test_typing_another_account_name_stands_an_armed_link_down(qapp: object) -> None:
    """The subject of the confirmation is the pair of accounts; a changed name is
    a different pair. `DISMISS_ALL_MOVED`'s finding, on this control."""
    stub = _StubParty()
    panel = _panel(stub)
    panel.character.setText("Pakka")
    panel.link_name.setText("FRIEND")
    panel.link_account()
    panel.link_name.setText("OTHER")

    panel.link_account()

    assert stub.linked == [], "the second press must not write the pair that was armed"
    assert panel.link_button.text() != LINK_IDLE or panel.report.text()


def test_an_empty_account_box_is_refused_here_and_nothing_is_asked(qapp: object) -> None:
    """The same rule as the empty character box: a press a person can take back
    should not cost a database read to be told about."""
    stub = _StubParty()
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.link_account()

    assert stub.planned == []
    assert panel.report.text() == NO_ACCOUNT_TYPED


def test_the_cap_line_is_the_seams_sentence_and_not_the_panels_arithmetic(
    qapp: object,
) -> None:
    """Round 1's second finding, at the widget end.

    The panel knows two numbers -- the bots it drew and the cap the seam read --
    and putting them beside each other would be the same false claim with a
    label making it: the module counts the bots it controls, which is not what
    the group table holds. So the note is the seam's own sentence, drawn
    verbatim.

    Mutation: rebuild the line here from `picked.added` and `picked.max_added`.
    The panel says "1 of 40 used" over a number that means something else.
    """
    note = "The server's added-bot cap (40) could not be checked from here."
    stub = _StubParty(
        picker=party.Picker(rows=(_candidate("Nore"),), added=1, max_added=40, note=note)
    )
    panel = _panel(stub)
    panel.character.setText("Pakka")

    panel.show_candidates()

    assert panel.candidate_note.text() == note
