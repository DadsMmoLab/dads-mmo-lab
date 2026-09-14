"""My Party's own surface (8.6): the seam the gate script drove, with buttons on it.

`PartyPanel` is the whole of My Party in the app. It does what
`.notes/gates/8.6-wotlk-yulon-ubuntu2-2026-09-09/gate86b.py` did on the live
server -- read the state, add a bot of a chosen class, see it in the party,
dismiss it -- through the same `MyPartySeam` object, because that gate's own
"What was NOT proved here" is *"The Qt widget for My Party does not exist"* and
the exit review names the consequence: a capability reachable only from a script
(`pyplan/phase8-exit-review-2026-09-09.md`, clause 3).

Call down / signal up (style-guide SS5): the panel calls `state()`, `add()` and
`remove()` on the seam it was handed, off the GUI thread through the runner it was
handed, and says `party_changed` when the group may have moved. It knows nothing
about databases, channels, containers or which view is showing it.

## What it does not do, and why

The prior art's own party page is bigger (`rust-main:launcher/src/lib/pages/Playerbots.svelte`),
and every part of it this panel leaves out is a part the seam has no method for.
Left out deliberately, each with the seam gap that decides it:

* **No picker for the MASTER's own name.** The seam reads one name's guid
  (`party.InstallParty.online_guid`) and has no "who is online" listing; the
  prior art's picker is fed by a separate CLI call (`Playerbots.svelte:75`
  `wowPartyOnline()`, offered as a `<select>` at `:390-398`). So the character
  is typed, and a name that is not in the world comes back as the seam's own
  sentence. T26 added a picker of a different kind beside it -- the characters
  this server would let that master ADD (`InstallParty.candidates`) -- which is
  a list of other people's characters and not a list of the master's own.
* **No "Enable My Party" / bridge deploy button.** `party.deploy()` exists and
  `InstallParty` does not expose it, so there is nothing to press through this
  seam. The panel shows WHICH precondition is unmet instead, which is the half
  that tells a person what to do -- and the prior art's own note is that a deploy
  button is how this feature last reported success for a no-op
  (`rust-main:crates/dml-wow/src/bridge.rs:56-70`).
* **No presets.** `party.py` has no preset store and the prior art's is a
  directory of files under `~/.dml` (`90-main.sh:4152-4230`), which is a feature
  rather than a control.

The spec, the level and "dismiss all" were the other three entries in that list
until T5 (2026-09-09), and each is now the seam's own method rather than a
second route: `specs()` reads this install's `playerbots.conf`, the level goes
through 8.4a's `set_level`, and `remove_all()` is `remove` per bot with each one
named.

T26 (2026-09-10) added the second way into a party, and both halves of it are
the seam's own methods for the same reason: "Add this character" offers the
characters `.playerbots bot add` would accept for this master
(`candidates()`, four database reads and this install's own `MaxAddedBots`) and
presses one through `add_named()`; "Link an account" writes the two rows that
open the friends-and-family route (`link_plan()` asks, `link_account()`
writes). What the module says about either goes to the master's game window and
never to this app, which is why the picker refuses in advance and the press
reports the group table rather than the command's own yes.

One thing the prior art does that this panel copies exactly: a bot that has not
arrived within the poll window is reported as NOT joined, with its cause
(`Playerbots.svelte:167`, the `SPAWNING_NOTE` branch of `add()`). Here that
sentence is `party.Addition`'s own and the panel only shows it.

## Two rules from `job.py` that this panel is bound by

The work runs through an injected `JobRunner`, and the callbacks are this class's
own bound `@Slot`s. Both halves matter: a plain function or lambda connected to a
worker's signal is delivered ON THE WORKER THREAD in PySide6, and a worker held
only by a local is collected before its thread runs it -- the GUI segfault
`job.py::InFlight` exists for.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from yulon import party
from yulon.log import get_logger
from yulon.ui.widgets.job import JobRunner

logger = get_logger(__name__)


class PartySeam(Protocol):
    """What this panel needs: one read and two presses.

    The widget-side half of `controller_view.MyPartySeam`, and the same three
    methods. Two declarations rather than one import because a widget may not
    import a view (style-guide SS3's layer rule, and `ui/widgets/*.py`'s own row
    in that table: a widget must not know which view is using it) -- a
    `TYPE_CHECKING` import would be the same knowledge with a flag on it. What
    keeps them honest is that the view hands its own object down: if the two
    ever disagree, mypy fails at that call, which is `_build_my_party_group`.

    Typed rather than `object`, which is what this was first written as. Three
    `# type: ignore[attr-defined]` comments were the price, and the thing they
    bought silence about is a method name typed wrongly here -- a panel that
    calls `self._seam.state()` on an object that spells it `party_state` fails
    where nothing checks the name, which is at the user's press. Measured:
    renaming `state` here to `party_state` makes mypy report both halves --
    `"PartySeam" has no attribute "state"` in this file, and
    `Argument 1 to "PartyPanel" has incompatible type "MyPartySeam"` at
    `controller_view.py`'s call.

    The RETURNS are `party`'s own dataclasses and not `object` either. That is
    not extra coupling -- this module already imports `party` for `BOT_CLASSES`
    and `MAX_NAME` -- and it is what lets the drawing slots cast once and then
    read `state.blocker` instead of `getattr(state, "blocker", "")`. A defensive
    `getattr` chain over a shape this file KNOWS is a chain that answers "" for
    a field somebody renamed, which is a refusal that quietly stops being shown.
    """

    def state(self, master: str) -> party.PartyState: ...

    def specs(self, klass: str) -> tuple[str, ...]: ...

    def max_level(self) -> int | None: ...

    def add(
        self,
        master: str,
        klass: str,
        *,
        gender: str = "",
        spec: str = "",
        level: int | None = None,
    ) -> party.Addition: ...

    def remove(self, master: str, bot: str) -> party.Dismissal: ...

    def remove_all(self, master: str, confirmed: tuple[int, ...]) -> party.MassDismissal: ...

    def candidates(self, master: str) -> party.Picker: ...

    def add_named(self, master: str, name: str) -> party.NamedAddition: ...

    def link_plan(self, master: str, account: str) -> party.AccountLink: ...

    def link_account(self, master: str, account: str) -> party.AccountLink: ...


DISMISS_NOTHING = "Dismiss"
"""What the dismiss button says with no bot chosen, and it is disabled saying it.

The Characters tab's rule, for its reason (`controller_view._CHARACTER_ACTIONS`):
a button called "Dismiss" is one somebody presses believing it acts on the row
they are looking at, so with a row chosen it says that row's name."""

NO_CHARACTER = (
    "Type the name of the character you are playing first. A bot is added to a live "
    "session -- the server resolves the master by name in the world -- so nothing was sent."
)
"""The refusal for an empty name field, and nothing is sent under it.

The seam would answer this too (`party.InstallParty.online_guid` returns None for
a name that is not a name, and `add()` turns that into its own sentence), but a
press with an empty box is a press a person can take back, and it costs a
database read and a `docker exec` to be told so."""

NO_BOT_CHOSEN = "Choose a bot in the party list first. Nothing was sent."

SPEC_AUTO = "let the server pick"
"""The first row of the spec picker, and the behaviour every add has had so far.

It carries `""` as its data, which is `party.add_bot`'s own word for "send
`talents autopick`". A picker whose only rows were real specs would be a control
with no way back to what the button did yesterday."""

LEVEL_AS_MADE = "leave it as the server made it"
"""The bottom of the level box, shown instead of the number 0.

A spin box has to hold some value and "no level was chosen" has to be one of
them, so the range starts below level 1 and wears these words there
(`QSpinBox.setSpecialValueText`). The alternative -- starting at 1 -- sends
level 1 for a box nobody touched, which resets every bot it is used on."""

NO_LEVEL_HERE = (
    f"No level can be chosen here: this server's own {party.MAX_LEVEL_KEY} could not be read out "
    f"of {party.WORLD_CONF}, so there is nothing to bound the box by. Setting that key and "
    "pressing Show this character's party again offers it."
)
"""The Characters tab's rule for a control this install cannot have (8.4d): not a
disabled box with no explanation, and not an empty space, but the sentence naming
what to fix. The key and the file are `party`'s own constants so this line cannot
name a path the reader does not read."""

DISMISS_ALL_IDLE = "Dismiss every bot…"
"""One button, two labels, and the armed one carries the count.

The tab's own gesture (`controller_view.REMOVE_IDLE`/`REMOVE_ARMED`) rather than
a second kind of confirmation: a user who has learned that pressing once only
arms is not surprised here. A modal would be the other option and it is the wrong
one twice over -- this panel's answers arrive from a worker thread, and the
prior art's own dismiss-all is a two-step in the page
(`Playerbots.svelte:204-220`)."""

NO_BOTS_TO_DISMISS = (
    "This character's party has no bots in it, so there is nothing to dismiss. Nothing was sent."
)
"""Refused here, and it never arms.

The same reason the empty character box is refused here: it costs a database read
and a `docker exec` to be told what the list on screen already says -- and arming
a button over an empty party is asking somebody to confirm nothing."""

DISMISS_ALL_CANCELLED = "Dismiss every bot was cancelled. Nothing was sent."
"""Said when a read stands an armed dismiss-all down.

It has to be SAID: a read of its own clears the report line, so without this an
armed press followed by Show this character's party would leave a disarmed button
and nothing anywhere saying the press had been dropped."""

PRESS_SHOW_FIRST = (
    "Press Show this character's party first: the party below was read for another character, "
    "so there is nothing confirmed to dismiss here. Nothing was sent."
)
"""The refusal for arming over somebody else's rows.

Round 2's second finding: Show as Pakka, retype the box as Anmi without pressing
Show, and the arm used to take `("Anmi", Pakka's rows)` -- a sentence naming
Pakka's bots over a press addressed to Anmi's party. Nothing had been armed, so
nothing stood down; what was missing was the rows remembering who they were drawn
for."""

DISMISS_ALL_MOVED = (
    "Dismiss every bot was not sent: the character or the party is not the one that was "
    "confirmed. Nothing was sent -- press it again to confirm what is on screen now."
)
"""The subject moved between the two presses, so the confirmation is void.

Round 1's finding, from both reviewers, and it is the whole reason an arm carries
a subject rather than a flag: armed on Pakka's two bots, a person who then types
another name into the character box and presses again used to send
`remove_all("Anmi")` -- a party that had never been shown, counted, or named in
the sentence they agreed to. The second press now requires the SAME normalised
master and the SAME bots the first press named, and anything else stands the arm
down with this line instead of firing."""

SHOW_CANDIDATES = "Show characters I can add"
"""The picker is READ on a press rather than filled when the panel opens.

It is four database reads -- the party, then the characters, the account names
and the playerbots tables -- and which characters this server would accept
changes with who is logged in, so a list drawn at start-up is a list that is
wrong by the time anybody looks at it. The same rule the rest of this panel
keeps: facts are re-read per press."""

ADD_NAMED_NOTHING = "Add this character…"
"""What the button says with no row chosen, and it is disabled saying it -- the
Dismiss button's rule (`DISMISS_NOTHING`), for its reason: with a row chosen it
says that row's name, so nobody presses it believing it acts on something
else."""

PRESS_SHOW_CANDIDATES_FIRST = (
    "Press Show characters I can add first: the list below was read for another character, so "
    "nothing in it was worked out for this one. Nothing was sent."
)
"""The rows and the character they were read FOR travel together, `_drawn`'s
rule: which characters may be added is decided per master -- own account, own
guild, own links -- so a list read as one and pressed as another is a press
nothing had worked out the rules for."""

NO_CANDIDATE_CHOSEN = "Choose a character in the list first. Nothing was sent."

LINK_IDLE = "Link an account…"
"""One button, two labels, and the armed one names the account. The tab's own
gesture again (`DISMISS_ALL_IDLE`): a person who has learned that pressing once
only arms is not surprised here, and a modal would be wrong twice over -- the
answers arrive from a worker thread, and this panel does not use them."""

NO_ACCOUNT_TYPED = (
    "Type the account name of the person whose characters you want to be able to add. It is the "
    "account name they log in with, not a character's name. Nothing was sent."
)

LINK_MOVED = (
    "Link an account was not sent: the character or the account is not the one that was "
    "confirmed. Nothing was written -- press it again to confirm what is on screen now."
)
"""The subject moved between the two presses, so the confirmation is void --
`DISMISS_ALL_MOVED`'s finding, on the control that writes a database row."""

WORKING = "Working -- the server is being asked. This can take a few seconds."
"""Shown while a press is in flight, and the buttons are disabled under it.

`party.add_bot` polls for up to six seconds (12 x 500 ms) before it will say a
bot did not arrive, so a person watching a panel that said nothing would press
again. A second press is refused rather than queued: the seam re-reads the
preconditions on every press and two adds in flight would poll each other's
bots."""


class PartyPanel(QWidget):
    """My Party for one install: a character, a class, the group, and a dismissal.

    `party_changed` is emitted after every press that REACHED the seam, so
    whatever else is showing the bots can re-read them (the users-surface
    design's own cross-link, `.notes/phase8-designs/b-users-surface.md:111`).
    Including the presses that came back refused, because a refusal is the
    seam's answer and not the panel's guess -- a `blocker` means nothing was
    sent, but a `joined=False` means the command WAS sent and the bot may still
    be walking in. A press this panel refused locally emits nothing: it never
    reached a server, so there is nothing for anybody to re-read.
    """

    party_changed = Signal()

    def __init__(
        self,
        seam: PartySeam,
        *,
        jobs: JobRunner,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._seam = seam
        self._jobs = jobs
        self._busy = False
        self._clear_report = False
        self._after_read = ""
        # What an armed "dismiss every bot" is ABOUT: the normalised master and
        # the bots the arming sentence named, snapshotted at the first press.
        # `None` is disarmed. A bare flag was round 1's defect -- it armed on one
        # party and fired on whatever the box said later.
        self._confirmed: tuple[str, tuple[int, ...]] | None = None
        # The rows the last successful read drew, and the character they were
        # drawn FOR. `Member`s rather than the strings on screen, because what a
        # confirmation is about is guids -- a name is what a display shows, and
        # `group_member` keys on the guid.
        self._drawn: tuple[party.Member, ...] = ()
        self._drawn_for = ""
        # Why the last arm was stood down, said on the NEXT press and then
        # forgotten. Empty for the ways a person cancels ON PURPOSE (the read,
        # or moving on to another button), which need no explanation and no
        # third press; set only where the subject changed underneath them, so
        # that press explains itself and leaves the button idle rather than
        # quietly starting a new confirmation about a different party.
        self._stood_down = ""
        # T26. The rows the picker last read and the character they were read
        # FOR, together for `_drawn`'s reason: which characters may be added is
        # decided per master, so a list that outlives the name it was read for
        # is a list about somebody else's rules.
        self._offered: tuple[party.Candidate, ...] = ()
        self._offered_for = ""
        # What an armed "Link an account" is ABOUT: the normalised master and
        # the account name the confirmation named. `None` is disarmed.
        self._link_confirmed: tuple[str, str] | None = None
        # Which spec request the picker is currently showing. Bumped per
        # request, checked at completion: an answer older than the newest
        # request is about a class the box no longer shows.
        self._spec_generation = 0

        self.character = QLineEdit(self)
        self.character.setPlaceholderText("the character you are playing, logged in")
        self.character.setMaxLength(party.MAX_NAME)
        self.character.returnPressed.connect(self.refresh_party)
        # Typing is not pressing, but it changes what a confirmed dismissal
        # would be about, so it stands one down. Every edit, not just Enter.
        self.character.textChanged.connect(self._character_edited)
        self.refresh_button = QPushButton("Show this character's party", self)
        self.refresh_button.clicked.connect(self.refresh_party)

        self.klass = QComboBox(self)
        # The server's own ten words, unmapped. A display table ("Death Knight"
        # for `dk`) would be a second list of classes to keep in step with
        # `party.BOT_CLASSES`, and the word that goes to the world server is
        # then not the word that was read on screen. `BOT_CLASSES` is this
        # tree's own measurement -- `dk` is in it because THIS tree's addclass
        # takes it, which the bash launcher's list deliberately did not.
        self.klass.addItems(party.BOT_CLASSES)
        self.spec = QComboBox(self)
        self.level = QSpinBox(self)
        self.level_absent = QLabel("", self)
        self.level_absent.setWordWrap(True)
        # Connected AFTER `spec` exists and after the class list was filled:
        # `addItems` moves the current index, and a handler wired before either
        # would read a spec box that has not been built yet.
        self.klass.currentTextChanged.connect(self._class_chosen)
        self.add_button = QPushButton("Add a bot", self)
        self.add_button.clicked.connect(self.add_bot)

        # T26, and BESIDE "Add a bot" rather than instead of it: that button
        # makes a bot of a chosen class, this one puts an existing character --
        # an alt on the same account, a guild mate, a friend's character on a
        # linked account -- into the party as a bot.
        self.candidates_button = QPushButton(SHOW_CANDIDATES, self)
        self.candidates_button.clicked.connect(self.show_candidates)
        self.candidate_list = QListWidget(self)
        self.candidate_list.setMinimumHeight(60)
        self.candidate_list.currentRowChanged.connect(self._candidate_chosen)
        self.candidate_note = QLabel("", self)
        self.candidate_note.setWordWrap(True)
        self.add_named_button = QPushButton(ADD_NAMED_NOTHING, self)
        self.add_named_button.clicked.connect(self.add_named)
        self.link_name = QLineEdit(self)
        self.link_name.setPlaceholderText("another player's account name")
        self.link_name.setMaxLength(party.MAX_ACCOUNT_NAME)
        # Typing is not pressing, but it changes which two accounts a confirmed
        # link would be about, so it stands one down. `character` does the same
        # from the other end -- the master decides whose account is linked.
        self.link_name.textChanged.connect(self._link_edited)
        self.link_button = QPushButton(LINK_IDLE, self)
        self.link_button.clicked.connect(self.link_account)

        self.member_list = QListWidget(self)
        self.member_list.setMinimumHeight(70)
        self.member_list.currentRowChanged.connect(self._member_chosen)
        self.dismiss_button = QPushButton(DISMISS_NOTHING, self)
        self.dismiss_button.clicked.connect(self.dismiss_bot)
        self.dismiss_all_button = QPushButton(DISMISS_ALL_IDLE, self)
        self.dismiss_all_button.clicked.connect(self.dismiss_all)

        self.check_list = QListWidget(self)
        self.check_list.setMinimumHeight(60)
        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.report = QLabel("", self)
        self.report.setWordWrap(True)
        self.report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        who = QHBoxLayout()
        who.setSpacing(6)
        who.addWidget(QLabel("Character", self))
        who.addWidget(self.character, 1)
        who.addWidget(self.refresh_button)

        # Compact 2-row layout for Class, Spec, Level and Add bot:
        # Row 1: Class + Spec pickers side-by-side
        row_class_spec = QHBoxLayout()
        row_class_spec.setSpacing(6)
        row_class_spec.addWidget(QLabel("Class", self))
        row_class_spec.addWidget(self.klass, 1)
        row_class_spec.addWidget(QLabel("Spec", self))
        row_class_spec.addWidget(self.spec, 1)

        # Row 2: Level spinner + Add a bot button
        row_lvl_add = QHBoxLayout()
        row_lvl_add.setSpacing(6)
        row_lvl_add.addWidget(QLabel("Level", self))
        row_lvl_add.addWidget(self.level, 1)
        row_lvl_add.addWidget(self.add_button, 1)

        named = QHBoxLayout()
        named.setSpacing(6)
        named.addWidget(self.candidates_button, 1)
        named.addWidget(self.add_named_button, 1)

        link = QHBoxLayout()
        link.setSpacing(6)
        link.addWidget(QLabel("Link account", self))
        link.addWidget(self.link_name, 1)
        link.addWidget(self.link_button)

        box = QVBoxLayout(self)
        box.setSpacing(6)
        box.setContentsMargins(4, 4, 4, 4)
        box.addLayout(who)
        box.addLayout(row_class_spec)
        box.addLayout(row_lvl_add)
        box.addWidget(self.level_absent)
        box.addLayout(named)
        box.addWidget(self.candidate_list, 1)
        box.addWidget(self.candidate_note)
        box.addLayout(link)

        # The two groups are headed by section labels (not bare body text) so
        # the long form reads as two distinct sections instead of one wall.
        in_party = QLabel("In the party now", self)
        in_party.setObjectName("section-title")
        box.addWidget(in_party)
        box.addWidget(self.summary)
        box.addWidget(self.member_list, 1)

        dismiss_row = QHBoxLayout()
        dismiss_row.setSpacing(6)
        dismiss_row.addWidget(self.dismiss_button, 1)
        dismiss_row.addWidget(self.dismiss_all_button, 1)
        box.addLayout(dismiss_row)

        needs = QLabel("What My Party needs", self)
        needs.setObjectName("section-title")
        box.addWidget(needs)
        box.addWidget(self.check_list, 1)
        box.addWidget(self.report)
        self._member_chosen(-1)
        self._candidate_chosen(-1)
        # Both are per-install readings and both are read again on every
        # "Show this character's party": a game installed, a conf edited or a
        # module deployed while this tab is open changes both answers, and this
        # module's own rule is that facts are re-read per press rather than
        # cached at start-up.
        self._load_level_bound()
        self._load_specs()

    # -- reads ---------------------------------------------------------------

    @Slot()
    def refresh_party(self) -> None:
        """Read this character's party, off the GUI thread. A press of its own.

        It is also the way out of an armed "dismiss every bot", which is what
        the tab's own armed paragraph tells people ("Press Refresh to cancel").

        The busy check is here as well as in `_master()` because this press
        starts three jobs and only one of them goes through that: a refresh
        while an add is polling would otherwise re-read the spec list and the
        level bound under it, which is work nobody asked for and a spec picker
        rebuilt mid-press.
        """
        if self._busy:
            return
        self._after_read = DISMISS_ALL_CANCELLED if self._stand_down("") else ""
        self._load_level_bound()
        self._load_specs()
        self._read(note=WORKING)

    def _load_specs(self) -> None:
        """Re-ask the seam which specs this install has for the chosen class.

        The answer carries the request that asked for it. Round 1's finding
        (Codex): each class change starts an independent read, the completions
        arrive in whatever order the runner finishes them, and a slower mage read
        landing after a druid read filled the DRUID picker with mage specs — so
        the next Add would send a spec `_spec_refusal` refuses, or worse, one
        this tree's module answers "not found" to in the game window where
        nothing can hear it.
        """
        self._spec_generation += 1
        asked = self._spec_generation
        klass = self.klass.currentText()
        self._read_alongside(lambda: (asked, klass, self._seam.specs(klass)), self._specs_read)

    @Slot(object)
    def _specs_read(self, result: object) -> None:
        """Fill the picker, keeping the chosen spec if the new class has it too.

        A completion older than the newest request is DROPPED rather than drawn:
        the generation is the panel's own counter, so "older" means "another
        request has been made since", which is exactly when this answer is about
        a class the box no longer shows.

        `_done()` is deliberately NOT called here: this is a read that rides
        along beside a press rather than one, and re-arming the buttons under an
        add that is still polling would let a second one start.
        """
        generation, klass, offered = cast("tuple[int, str, tuple[str, ...]]", result)
        if generation != self._spec_generation:
            logger.info(f"dropped a stale spec reading for {klass}: {len(offered)} names")
            return
        wanted = self.spec.currentData()
        self.spec.clear()
        self.spec.addItem(SPEC_AUTO, "")
        for name in offered:
            self.spec.addItem(name, name)
        if wanted:
            found = self.spec.findData(wanted)
            self.spec.setCurrentIndex(max(found, 0))

    def _load_level_bound(self) -> None:
        self._read_alongside(self._seam.max_level, self._level_bound_read)

    @Slot(object)
    def _level_bound_read(self, result: object) -> None:
        """Bound the box by this server's own top level, or offer no box at all.

        80 is nowhere in this panel. A cap that could not be read is not a cap of
        80 — the sentence names the key to set, and the box stays out of reach
        until it is, because a level sent against a bound nobody knows is a
        number this app made up.
        """
        top = cast("int | None", result)
        self.level.setSpecialValueText(LEVEL_AS_MADE)
        if top is None:
            self.level.setRange(0, 0)
            self.level.setEnabled(False)
            self.level_absent.setText(NO_LEVEL_HERE)
            self.level_absent.setVisible(True)
            return
        self.level.setRange(0, top)
        # Not `True`: this reading rides along beside a press, and a press locks
        # every picker (`_arm`). Re-enabling here would hand the level box back
        # under an add that is still polling (round 2, optional).
        self.level.setEnabled(not self._busy)
        self.level_absent.setText("")
        self.level_absent.setVisible(False)

    def _read(self, *, note: str | None) -> None:
        """Read the group. `note` is `None` for the re-read that FOLLOWS a press.

        A press says two things -- what the server did, and what the group table
        holds afterwards -- and the second must not overwrite the first. Before
        this split the panel ended every add reading "1 bot in this party", which
        had thrown away the only sentence saying whether the bot was geared, or
        whether the server took the command and no bot ever arrived.
        """
        master = self._master()
        if master is None:
            return
        # A read that is its own press owns the report line, so its outcome has
        # to CLEAR it: the whole answer to "show me this party" is the summary
        # and the checks, and a `Working…` left standing over them is a panel
        # that looks like it is still asking.
        self._clear_report = note is not None
        self._start(note)
        # The master travels WITH the answer. The rows a person confirms have to
        # be attributable to a character, and reading `self.character` again in
        # the slot would attribute them to whatever the box says by then --
        # which is round 2's second finding with the check moved rather than
        # fixed.
        self._run(lambda: (master, self._seam.state(master)), self._state_read)

    @Slot(str)
    def _class_chosen(self, _klass: str) -> None:
        """A different class has a different spec list, so the picker is re-read."""
        self._load_specs()

    @Slot(object)
    def _state_read(self, result: object) -> None:
        """Draw the group, or the precondition that stopped it.

        The rows are cleared FIRST and drawn only from what this reading holds:
        a refusal beside the last successful reading's rows is a panel saying
        "there is no bridge" over a party it is still showing.

        The parameter is `object` because that is what a `JobRunner` carries --
        one runner delivers every job's result -- and it is cast once here, on
        the line below, rather than read field by field through `getattr`. Every
        view slot in this app is written the same way.
        """
        master, state = cast("tuple[str, party.PartyState]", result)
        self._done()
        if self._clear_report:
            # Empty for an ordinary read, and the cancellation notice for the
            # one that stood an armed dismiss-all down: that press left nothing
            # else anywhere to say it had been dropped.
            self.report.setText(self._after_read)
            self._after_read = ""
        self.member_list.clear()
        # The rows and the character they belong to go together, always: a list
        # that outlives the name it was read for is what let an arm take
        # ("Anmi", Pakka's bots). Cleared here and set only where rows are
        # actually drawn, so a refused read leaves nothing to confirm.
        self._drawn = ()
        self._drawn_for = ""
        self._member_chosen(-1)
        self.check_list.clear()
        for check in state.checks:
            self.check_list.addItem("ok " + check.name if check.met else f"not yet: {check.name}")
            if not check.met:
                self.check_list.item(self.check_list.count() - 1).setToolTip(check.sentence)
        if state.blocker or state.problem:
            self.summary.setText(state.blocker or state.problem)
            return
        members = state.members
        self._drawn = members
        self._drawn_for = master
        for member in members:
            self.member_list.addItem(self._row(member))
        self.summary.setText(
            "This character's party has no bots in it yet."
            if not members
            else f"{len(members)} {'bot' if len(members) == 1 else 'bots'} in this party."
        )

    @staticmethod
    def _row(member: party.Member) -> str:
        """One group row. The class is the NUMBER the table holds.

        `characters.class` is an id and this app has no measured id-to-name table
        for this tree; the prior art has one in the browser
        (`rust-main:launcher/src/lib/wow.ts` `className`). A wrong class name
        beside a bot is worse than the number that was actually read, so the
        number is what is shown until a box measures the mapping.
        """
        return f"{member.name} — level {member.level} — class {member.klass}"

    # -- presses -------------------------------------------------------------

    @Slot()
    def add_bot(self) -> None:
        """Add one bot of the chosen class, spec and level to this character's party.

        The spec and the level are both optional and both say so in their own
        control: `SPEC_AUTO` carries `""`, which is the `talents autopick`
        whisper this button has always sent, and the level box's bottom step
        carries `LEVEL_AS_MADE`, which sends no level at all.
        """
        master = self._master()
        if master is None:
            return
        self._stand_down("")
        klass = self.klass.currentText()
        spec = cast("str | None", self.spec.currentData()) or ""
        level = self.level.value() or None
        self._start(WORKING)
        self._run(
            lambda: self._seam.add(master, klass, spec=spec, level=level),
            self._added,
        )

    @Slot(object)
    def _added(self, result: object) -> None:
        """Say what the press did, then re-read the group rather than assume it.

        The panel never adds the row itself. `Addition` has four states and two
        of them -- the command accepted with no bot arriving, and a precondition
        that stopped it -- are exactly the ones a panel that drew its own row
        would report as a party member who is not there.
        """
        addition = cast(party.Addition, result)
        self._done()
        self.report.setText(addition.sentence)
        self._read(note=None)
        self.party_changed.emit()

    # -- T26: the named character, and the account link ----------------------

    @Slot()
    def show_candidates(self) -> None:
        """Read which characters this server would let this master add.

        A press of its own, and it carries the master with the answer for
        `_read`'s reason: the rows a person chooses from have to be
        attributable to the character they were worked out for, and reading the
        box again in the slot would attribute them to whatever it says by then.
        """
        master = self._master()
        if master is None:
            return
        self._start(WORKING)
        self._run(lambda: (master, self._seam.candidates(master)), self._candidates_read)

    @Slot(object)
    def _candidates_read(self, result: object) -> None:
        """Draw every row, with the refused ones greyed and their reason on them.

        Refused rows are SHOWN rather than filtered out: "why is my alt not in
        this list" is the question a filtered list cannot answer, and the
        server's own refusal never reaches this app -- it is printed in the
        master's game window (`PlayerbotMgr.cpp:115`, `:134`, `:686`). A row
        that cannot be pressed says which rule shut it out instead.
        """
        master, picked = cast("tuple[str, party.Picker]", result)
        self._done()
        self.candidate_list.clear()
        self._offered = ()
        self._offered_for = ""
        self._candidate_chosen(-1)
        if picked.problem:
            self.candidate_note.setText(picked.problem)
            return
        self._offered = picked.rows
        self._offered_for = master
        for row in picked.rows:
            self.candidate_list.addItem(self._candidate_row(row))
            item = self.candidate_list.item(self.candidate_list.count() - 1)
            if not row.allowed:
                # Greyed AND unselectable: a row the server would refuse is not
                # a row to press, and a disabled Add button under a selected
                # refusal would say the same thing twice and less clearly.
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        # `Picker.note` and nothing of this panel's own arithmetic: the count
        # the seam read is the group table's, which is not the number the
        # module caps (round 1's second finding), and a line here that put the
        # two beside each other would be the same claim with a widget making
        # it.
        self.candidate_note.setText(picked.note)

    @staticmethod
    def _candidate_row(row: party.Candidate) -> str:
        """One offered character. The class is the NUMBER the table holds, for
        `_row`'s reason: this app has no measured id-to-name table for this
        tree, and a wrong class name beside a character is worse than the
        number that was actually read."""
        return (
            f"{row.name} — level {row.level} — class {row.klass} — {row.account_or_guild} — "
            f"{row.allowed_by or row.refused_because}"
        )

    @Slot(int)
    def _candidate_chosen(self, row: int) -> None:
        """Name the character on the button, and disable it where there is none."""
        chosen = self._chosen_candidate() if row >= 0 else None
        self.add_named_button.setText(
            ADD_NAMED_NOTHING if chosen is None else f"Add {chosen.name} as a bot"
        )
        self.add_named_button.setEnabled(chosen is not None and not self._busy)

    def _chosen_candidate(self) -> party.Candidate | None:
        """The row a person chose, or `None` -- never a refused one.

        By index into the rows the read returned rather than by parsing the text
        on screen: what the seam is handed is the character the picker worked
        the rules out for, and a name read back off a label is a name a display
        format can change.
        """
        row = self.candidate_list.currentRow()
        if not 0 <= row < len(self._offered):
            return None
        chosen = self._offered[row]
        return chosen if chosen.allowed else None

    @Slot()
    def add_named(self) -> None:
        """Put the chosen existing character into this character's party."""
        master = self._master()
        if master is None:
            return
        if self._offered_for != master:
            self.report.setText(PRESS_SHOW_CANDIDATES_FIRST)
            return
        chosen = self._chosen_candidate()
        if chosen is None:
            self.report.setText(NO_CANDIDATE_CHOSEN)
            return
        self._stand_down("")
        self._stand_link_down()
        name = chosen.name
        self._start(WORKING)
        self._run(lambda: self._seam.add_named(master, name), self._added_named)

    @Slot(object)
    def _added_named(self, result: object) -> None:
        """The seam's own sentence, then the group table read back.

        The panel never draws the row itself, `_added`'s rule: this route has
        four outcomes too, and one of them -- the command issued and no bot
        arriving -- is the ordinary shape of a refusal here, because the module
        answers this command only in the master's game window.
        """
        self.report.setText(cast(party.NamedAddition, result).sentence)
        self._done()
        self._read(note=None)
        self.party_changed.emit()

    @Slot()
    def link_account(self) -> None:
        """Ask on the first press, write on the second -- the SAME two accounts.

        The first press is a READ: it resolves both accounts and refuses an
        account this server does not have, the master's own, or a pair that is
        already linked, so nobody is asked to confirm a write that cannot
        happen. The second press hands the same pair to the seam, which resolves
        them again -- an account can be renamed, deleted or linked between two
        presses, and the confirmation is about what was on screen, not about
        what is true when the row is written.
        """
        master = self._master()
        if master is None:
            return
        account = self.link_name.text().strip()
        if not account:
            self._stand_link_down()
            self.report.setText(NO_ACCOUNT_TYPED)
            return
        if self._link_confirmed is not None and self._link_confirmed != (master, account):
            # The subject moved since the confirmation. Say so and stay idle:
            # re-arming here would start a NEW confirmation about a different
            # pair off a press meant for the old one.
            self._stand_link_down()
            self.report.setText(LINK_MOVED)
            return
        armed = self._link_confirmed is not None
        self._stand_down("")
        self._start(WORKING)
        if armed:
            self._stand_link_down()
            self._run(lambda: self._seam.link_account(master, account), self._linked)
            return
        self._run(lambda: (master, account, self._seam.link_plan(master, account)), self._planned)

    @Slot(object)
    def _planned(self, result: object) -> None:
        """Arm on what the seam says it WOULD write, and never on a refusal."""
        master, account, plan = cast("tuple[str, str, party.AccountLink]", result)
        self._done()
        self.report.setText(plan.sentence)
        if plan.blocker:
            self._stand_link_down()
            return
        self._link_confirmed = (master, account)
        self.link_button.setText(f"Press again to link {account}")

    @Slot(object)
    def _linked(self, result: object) -> None:
        """What the write did, in the seam's words. No group read follows it:
        a link changes what MAY be added, not what is in the party."""
        self._done()
        self.report.setText(cast(party.AccountLink, result).sentence)

    @Slot(str)
    def _link_edited(self, _text: str) -> None:
        """A different account name is a different pair, so any arm goes."""
        self._stand_link_down()

    def _stand_link_down(self) -> None:
        self._link_confirmed = None
        self.link_button.setText(LINK_IDLE)

    @Slot()
    def dismiss_all(self) -> None:
        """Arm on the first press, send every bot away on the second — the SAME one.

        The arm carries its subject: the normalised master and the guids of the
        bots this panel named in the sentence a person agreed to. The second
        press hands that snapshot to the seam, which reads the group table
        again and refuses unless the party is still exactly that set; a changed
        name in the box stands the arm down, and rows drawn for another
        character cannot be armed at all (`PRESS_SHOW_FIRST`). Round
        1's finding is the reason, and it is not theoretical -- armed on Pakka's
        two bots, typing another name and pressing again used to dismiss the
        party of a character who had never been on screen.

        The names are the list the seam last read, and the seam reads the group
        table again for itself when it fires: it is that reading, not this one,
        that decides who is actually sent away. This one decides what was
        CONFIRMED, which is a different question and the only one a person can
        answer.

        The confirmed guids travel WITH the second press, and the seam re-reads
        the group table and refuses unless it is still exactly that set. Round
        2's must-fix, and the panel's own check is not what makes it safe: this
        one compares what is DRAWN, and a bot that joined between the two
        presses changes nothing on screen. Only the end that can read the party
        can enforce what was agreed to.

        No wall-clock expiry, deliberately. The hazard an expiry addresses is an
        arm that goes stale while nobody is looking, and staleness that matters
        is now caught where it happens: the seam's re-read refuses a party that
        moved, whether it moved in one second or ten minutes. A clock would be a
        seam this widget has no other use for, and it would refuse presses that
        are still perfectly good.
        """
        master = self._master()
        if master is None:
            return
        if self._stood_down:
            # The subject moved since the confirmation. Say so and stay idle:
            # re-arming here would start a NEW confirmation about a different
            # party off a press that was meant for the old one.
            said, self._stood_down = self._stood_down, ""
            self.report.setText(said)
            return
        # `_drawn` and not `member_list.count()`: one source of truth for what a
        # confirmation is about. Counting the widget and confirming the reading
        # is two answers to one question, and it made the clearing in
        # `_state_read` unfalsifiable -- a stale `_drawn` behind an empty list
        # was covered by the count rather than by the thing that keeps it true.
        if not self._drawn:
            self._stand_down("")
            self.report.setText(NO_BOTS_TO_DISMISS)
            return
        if self._drawn_for != master:
            # Rows read for somebody else. Nothing was armed, so nothing stood
            # down, and without this the arm would take this character's name
            # and that character's bots.
            self._stand_down("")
            self.report.setText(PRESS_SHOW_FIRST)
            return
        subject = (master, tuple(member.guid for member in self._drawn))
        if self._confirmed is None:
            self._confirmed = subject
            count = len(self._drawn)
            self.dismiss_all_button.setText(f"Press again to dismiss {party.bots_word(count)}")
            self.report.setText(
                f"This uninvites {party.bots_word(count)} from {master}'s party -- "
                f"{', '.join(member.name for member in self._drawn)} -- and whispers each one to "
                "log out. Press Show this character's party to cancel."
            )
            return
        # No second comparison of `subject` against `self._confirmed` here, and
        # its absence is deliberate rather than an omission: `_drawn` is written
        # only by `_state_read`, every path that reaches `_state_read` stands
        # the arm down first, and a master that changed is refused two lines
        # above. A branch on it could not be reached, and an unreachable guard
        # is one that looks like protection while the real hole -- a party that
        # moved on the SERVER, invisible here -- stays open. That one is the
        # seam's, which re-reads and refuses.
        confirmed = self._confirmed[1]
        self._stand_down("")
        self._start(WORKING)
        self._run(lambda: self._seam.remove_all(master, confirmed), self._dismissed_all)

    @Slot(str)
    def _character_edited(self, _text: str) -> None:
        """A different name in the box is a different party, so any arm goes.

        Both arms: the master decides whose account a link would be written for,
        so a changed character is a changed pair as surely as a changed account
        name is.
        """
        self._stand_link_down()
        if self._stand_down(DISMISS_ALL_MOVED):
            self.report.setText(DISMISS_ALL_MOVED)

    @Slot(object)
    def _dismissed_all(self, result: object) -> None:
        """One sentence naming every bot, then the group table read back."""
        self._done()
        self.report.setText(cast(party.MassDismissal, result).sentence)
        self._read(note=None)
        self.party_changed.emit()

    def _stand_down(self, reason: str) -> bool:
        """Drop any armed dismiss-all, and say whether there was one.

        `reason` is what the NEXT press will say before it refuses to fire —
        empty where the person cancelled on purpose (a read, or another button),
        because that needs no explanation and no third press.
        """
        was_armed = self._confirmed is not None
        self._confirmed = None
        self._stood_down = reason if was_armed else ""
        self.dismiss_all_button.setText(DISMISS_ALL_IDLE)
        return was_armed

    @Slot()
    def dismiss_bot(self) -> None:
        """Send the chosen bot away."""
        master = self._master()
        if master is None:
            return
        bot = self._chosen_bot()
        if bot is None:
            self.report.setText(NO_BOT_CHOSEN)
            return
        # Silently, unlike the read's cancellation: this press has an answer of
        # its own and a cancellation notice over it would throw away the only
        # sentence saying what happened to the bot.
        self._stand_down("")
        self._start(WORKING)
        self._run(
            lambda: self._seam.remove(master, bot),
            self._dismissed,
        )

    @Slot(object)
    def _dismissed(self, result: object) -> None:
        self._done()
        self.report.setText(cast(party.Dismissal, result).sentence)
        self._read(note=None)
        self.party_changed.emit()

    @Slot(object)
    def _failed(self, exc: object) -> None:
        """A PRESS whose seam raised. One sentence, and the panel stays usable."""
        self._done()
        logger.warning(f"My Party could not ask the server: {type(exc).__name__}: {exc}")
        self.report.setText(f"My Party could not ask the server: {exc}")
        self.party_changed.emit()

    @Slot(object)
    def _side_failed(self, exc: object) -> None:
        """A side READING whose seam raised — the spec list, or the level bound.

        It must NOT call `_done()`. Those two reads ride along beside a press
        rather than being one, and `_done()` re-arms every button: a `specs()`
        that raised while an add was still polling would unlock the buttons
        under it and let a second add start, which is the thing `WORKING` and
        `_busy` exist to prevent. Round 1 flagged it (not blocking) and it is a
        slot rather than a comment because that is what makes it impossible.

        The report line is left alone while a press owns it, for the same
        reason: the press's own sentence is the one a person is waiting for.
        """
        logger.warning(f"My Party could not read this install: {type(exc).__name__}: {exc}")
        if not self._busy:
            self.report.setText(f"My Party could not read this install: {exc}")

    # -- the small print -----------------------------------------------------

    def _master(self) -> str | None:
        """The name in the box, or `None` with the refusal already on screen."""
        if self._busy:
            return None
        master = self.character.text().strip()
        if not master:
            self.report.setText(NO_CHARACTER)
            return None
        return master

    def _chosen_bot(self) -> str | None:
        item = self.member_list.currentItem()
        if item is None:
            return None
        # The row's own text is "<name> — level …"; the name is what the seam
        # takes, and it is read back off the row rather than kept in a parallel
        # list that could drift out of step with what is on screen.
        return item.text().split(" — ")[0]

    @Slot(int)
    def _member_chosen(self, row: int) -> None:
        """Name the bot on the button, and disable it where there is none."""
        bot = self._chosen_bot() if row >= 0 else None
        self.dismiss_button.setText(DISMISS_NOTHING if bot is None else f"Dismiss {bot}")
        self.dismiss_button.setEnabled(bot is not None and not self._busy)

    def _run(self, work: Callable[[], object], on_done: Callable[[object], None]) -> None:
        self._jobs(work, on_done, self._failed)

    def _read_alongside(
        self, work: Callable[[], object], on_done: Callable[[object], None]
    ) -> None:
        """A reading that is not a press: it fails without unlocking the panel."""
        self._jobs(work, on_done, self._side_failed)

    def _start(self, note: str | None) -> None:
        self._busy = True
        if note is not None:
            self.report.setText(note)
        self._arm(False)

    def _done(self) -> None:
        self._busy = False
        self._arm(True)

    def _arm(self, on: bool) -> None:
        """Every control a press would change the meaning of, locked while it runs.

        The three pickers as well as the buttons (round 1, not blocking): the
        class box changing mid-press re-reads the spec list under an add that
        has already sent its class, and a level or spec chosen while the server
        is being asked is one the answer on screen will not be about. The level
        box also obeys its own bound -- re-enabling it here on an install whose
        `MaxPlayerLevel` could not be read would offer a control
        `_level_bound_read` had just withheld.
        """
        self.refresh_button.setEnabled(on)
        self.add_button.setEnabled(on)
        self.dismiss_button.setEnabled(on and self.member_list.currentItem() is not None)
        self.dismiss_all_button.setEnabled(on)
        self.klass.setEnabled(on)
        self.spec.setEnabled(on)
        self.level.setEnabled(on and self.level.maximum() > 0)
        self.candidates_button.setEnabled(on)
        self.add_named_button.setEnabled(on and self._chosen_candidate() is not None)
        self.link_button.setEnabled(on)
        self.link_name.setEnabled(on)
