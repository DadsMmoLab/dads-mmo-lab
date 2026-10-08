"""Tests for `InstallPlay` — the object the Characters tab presses (8.4a).

The seam where a read and a write meet, which is where this phase's defects have
lived. Every action canonicalises the name first, because the server is as
case-sensitive as its own column, and every action reports all three outcomes.
"""

from __future__ import annotations

import pytest

from yulon import play
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")


class _Reader:
    """A SQL seam that answers by matching on the statement."""

    def __init__(self, **answers: str) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def query(self, db: str, statement: str) -> str:
        self.asked.append(statement)
        for key, answer in self.answers.items():
            if key in statement:
                return answer
        return ""

    def run_statement(self, db: str, statement: str) -> None:  # pragma: no cover
        raise AssertionError("a read must not write")


class _Channel:
    """A channel that records what it was sent and answers from a script."""

    def __init__(self, *outcomes: str, text: str = "done") -> None:
        self.outcomes = list(outcomes)
        self.text = text
        self.sent: list[str] = []

    def send(self, command: str) -> object:
        self.sent.append(command)
        outcome = self.outcomes.pop(0) if self.outcomes else "yes"
        return type(
            "Answer",
            (),
            {
                "outcome": outcome,
                "text": self.text if outcome == "yes" else "the server said no",
                "reason": "" if outcome != "unknown" else "the server could not be reached",
                "indeterminate": False,
                "denied": False,
            },
        )()


def _install(tmp_path, sql=None, channel=None) -> play.InstallPlay:
    return play.InstallPlay(
        WOTLK,
        tmp_path,
        sql=sql if sql is not None else _Reader(**{"SELECT name, online FROM": "Guglu\t1\n"}),
        channel_for_saved=lambda: channel if channel is not None else _Channel(),
    )


# -- the name is the server's, not the typist's -----------------------------


def test_every_action_sends_the_stored_spelling_of_the_name(tmp_path) -> None:
    """Typed `guglu`, stored `Guglu`, and the command carries `Guglu`.

    The prior art's own bug, and it does not fail loudly: the server answers
    "not online" for a character who is standing in Stormwind.
    """
    channel = _Channel()
    install = _install(tmp_path, channel=channel)

    install.teleport("guglu", "Stormwind")
    install.set_level("GUGLU", 80)
    install.rename("gUgLu")
    install.revive("guglu")

    assert channel.sent == [
        "teleport name Guglu Stormwind",
        "character level Guglu 80",
        "character rename Guglu",
        "revive Guglu",
    ]


def test_a_character_nobody_has_is_refused_before_the_server_is_asked(tmp_path) -> None:
    """And the refusal names what was typed, because that is what the person
    can see and correct."""
    channel = _Channel()
    install = _install(tmp_path, sql=_Reader(), channel=channel)

    outcome = install.teleport("nobodyhere", "Stormwind")

    assert outcome.done is False
    assert "nobodyhere" in outcome.problem
    assert channel.sent == [], "a name that does not exist reached the server"


# -- the three outcomes -----------------------------------------------------


def test_a_command_the_server_refused_is_reported_as_a_refusal(tmp_path) -> None:
    install = _install(tmp_path, channel=_Channel("no"))

    outcome = install.revive("guglu")

    assert outcome.done is False
    assert outcome.indeterminate is False


def test_a_channel_that_could_not_ask_says_so_rather_than_that_it_failed(tmp_path) -> None:
    install = _install(tmp_path, channel=_Channel("unknown"))

    outcome = install.set_level("guglu", 10)

    assert outcome.done is False
    assert "could not be reached" in outcome.problem


# -- mail -------------------------------------------------------------------


def test_mailed_gold_is_turned_into_copper_once(tmp_path) -> None:
    """The button says gold because that is what a person has; the server counts
    copper. One multiplication, in one place."""
    channel = _Channel()
    install = _install(tmp_path, channel=channel)

    install.mail_gold("guglu", gold=5, subject="Wages", body="Well earned")

    assert channel.sent == ['send money Guglu "Wages" "Well earned" 50000']


def test_a_gear_set_larger_than_one_mail_is_sent_as_the_mails_it_needs(tmp_path) -> None:
    """Nineteen pieces and a cap of twelve is two mails, and the second carries
    the remaining seven. The definition of done says the button promises a
    number before the press, so the number comes from the same function that
    does the sending.
    """
    nineteen = "\n".join(str(6000 + n) for n in range(19))
    sql = _Reader(**{"SELECT name, online FROM": "Guglu\t1\n", "character_inventory": nineteen})
    channel = _Channel()
    install = _install(tmp_path, sql=sql, channel=channel)

    outcome = install.send_gear_set("guglu", to="guglu", subject="Set", body="Wear it")

    assert outcome.done is True
    assert len(channel.sent) == 2, channel.sent
    assert channel.sent[0].count(":1") == 12
    assert channel.sent[1].count(":1") == 7


def test_the_number_of_mails_a_set_needs_can_be_asked_before_pressing(tmp_path) -> None:
    nineteen = "\n".join(str(6000 + n) for n in range(19))
    sql = _Reader(**{"SELECT name, online FROM": "Guglu\t1\n", "character_inventory": nineteen})
    install = _install(tmp_path, sql=sql)

    assert install.gear_set_size("guglu") == (19, 2)


def test_a_gear_set_from_a_character_wearing_nothing_is_refused(tmp_path) -> None:
    """Rather than sending an empty mail, which the server would refuse anyway
    with a sentence about item ids that says nothing about gear."""
    sql = _Reader(**{"SELECT name, online FROM": "Guglu\t1\n"})
    channel = _Channel()
    install = _install(tmp_path, sql=sql, channel=channel)

    outcome = install.send_gear_set("guglu", to="guglu", subject="s", body="b")

    assert outcome.done is False
    assert "nothing" in outcome.problem.lower() or "no items" in outcome.problem.lower()
    assert channel.sent == []


def test_a_set_that_fails_half_way_says_which_mails_went(tmp_path) -> None:
    """Two mails, the second refused. Reporting a plain failure would have a
    person send the whole set again and the recipient get the first twelve
    twice."""
    nineteen = "\n".join(str(6000 + n) for n in range(19))
    sql = _Reader(**{"SELECT name, online FROM": "Guglu\t1\n", "character_inventory": nineteen})
    channel = _Channel("yes", "no")
    install = _install(tmp_path, sql=sql, channel=channel)

    outcome = install.send_gear_set("guglu", to="guglu", subject="s", body="b")

    assert outcome.done is False
    assert "1" in outcome.problem and "2" in outcome.problem, outcome.problem


# -- what the tab may draw --------------------------------------------------


def test_a_tree_with_no_measured_play_block_offers_nothing(tmp_path) -> None:
    """The tab draws what the tree has, and a button drawn on a tree nobody has
    asked would send a command nobody has checked.

    The unmeasured tree here was Vanilla until 8.4c measured it, and Tortoise
    carries a block written from its source ahead of 8.4d's gate -- which has
    not run, so this sentence must not say it did (audit, 2026-09-08).
    Every shipped entry now carries a block, so the tree that
    stands here is SYNTHESISED -- which is what this guard's own note said to do
    when the last real one went, rather than delete the assertion the whole
    `NotMeasured` class exists for. The second half is what keeps it honest: it
    asserts against the shipped catalog that there is no longer a real tree to
    stand here, so the day a fifth game lands without its 8.4 box this test says
    so rather than passing on a stand-in.
    """
    unmeasured = _unmeasured()

    assert play.InstallPlay.for_entry_is_possible(unmeasured) is False
    assert play.InstallPlay.for_entry_is_possible(WOTLK) is True
    assert [
        entry.id
        for entry in load_catalog().games
        if not play.InstallPlay.for_entry_is_possible(entry)
    ] == []


def test_the_mail_cap_offered_is_the_one_this_tree_carries(tmp_path) -> None:
    install = _install(tmp_path)

    assert install.mail_item_cap == 12


def test_an_unmeasured_tree_has_no_cap_to_offer_rather_than_a_plausible_one(
    tmp_path,
) -> None:
    """The fallback answered 1, which stopped being a refusal on 2026-09-07.

    It was written when no tree carried a cap of one, so the number could only
    mean "nobody measured this". Vanilla's is 1 (`Mail.h:49`), so from 8.4c an
    unmeasured tree and a measured one answer the same thing at this property —
    and a gear button on the unmeasured one would promise a mail per piece with
    nothing behind the promise. It raises instead, in the same voice
    `play.equipped` already refuses in.
    """
    unmeasured = _unmeasured()
    install = play.InstallPlay(
        unmeasured, tmp_path, sql=_Reader(), channel_for_saved=lambda: _Channel()
    )

    with pytest.raises(play.NotMeasured) as refused:
        install.mail_item_cap  # noqa: B018

    assert unmeasured.name in str(refused.value)


def test_the_cap_is_this_trees_own_and_not_its_siblings() -> None:
    """Same core, same command, different number — asserted in one breath.

    Read on m910q, 2026-09-07, from the two installs' own checkouts: Vanilla's
    `src/mangos-classic/src/game/Mails/Mail.h:49` is `#define MAX_MAIL_ITEMS 1`
    and TBC's `src/mangos-tbc/src/game/Mails/Mail.h:49`, the same line of the
    same header, is `12`. The server enforces it at
    `src/game/Chat/Level3.cpp:6393` and, being a `return false`, the refusal
    reaches SOAP as a closed connection rather than as a sentence (8.3b).

    Both halves are in ONE test on purpose. 8.4c's whole hazard is pasting the
    sibling's block over, which would leave a Vanilla cap that reads 12 and a
    gear button promising two mails for a server that will send neither.
    """
    catalog = load_catalog()

    assert catalog.get("wow-vanilla").play.mail_item_cap == 1
    assert catalog.get("wow-tbc").play.mail_item_cap == 12


@pytest.mark.parametrize("level", [0, 256])
def test_a_level_this_command_does_not_take_is_refused_with_its_reason(
    tmp_path, level: int
) -> None:
    install = _install(tmp_path, channel=_Channel())

    outcome = install.set_level("guglu", level)

    assert outcome.done is False
    assert str(level) in outcome.problem


def test_a_half_sent_set_says_what_arrived_and_does_not_advise_the_impossible(
    tmp_path,
) -> None:
    """8.4a's adversarial review: the sentence offered an operation nobody has.

    "send only what is missing" is not a thing this app can do — there is one
    button and it sends everything worn — so a person following that advice
    presses it again and the recipient gets the first twelve items twice.

    What the sentence can honestly do is say which mails arrived, that pressing
    again sends the whole set from the start, and that the ones already
    delivered will arrive a second time if they do.
    """
    nineteen = "\n".join(str(6000 + n) for n in range(19))
    sql = _Reader(**{"SELECT name, online FROM": "Guglu\t1\n", "character_inventory": nineteen})
    install = _install(tmp_path, sql=sql, channel=_Channel("yes", "no"))

    outcome = install.send_gear_set("guglu", to="guglu", subject="s", body="b")

    assert outcome.done is False
    said = outcome.problem.lower()
    assert "1 of 2" in said or "1 of the 2" in said, outcome.problem
    assert "again" in said, "it does not say what pressing again would do"
    assert "missing" not in said, "it still advises an operation that does not exist"


def _unmeasured():
    """A shipped entry with its Play block taken away.

    Written once and used by both guards. `model_copy` keeps the id, so this is
    still an entry this build has a factory for -- a tree whose 8.4 box has not
    been done, not a game this build cannot manage.
    """
    return WOTLK.model_copy(update={"play": None})


# -- 8.4d: the tortoise fork's own verbs -------------------------------------


TORTOISE = load_catalog().get("wow-tortoise")


def _tortoise(tmp_path, channel):
    return play.InstallPlay(
        TORTOISE,
        tmp_path,
        sql=_Reader(**{"SELECT name, online FROM": "Guglu\t1\n"}),
        channel_for_saved=lambda: channel,
    )


def test_the_rename_this_tree_sends_is_its_own_top_level_command(tmp_path) -> None:
    """Read from this fork's source, 2026-09-07: `rename` is registered at the
    TOP level (`src/game/Chat/Chat.cpp:850`) and its `characterCommandTable` has
    no rename row at all.

    Both trees are asserted against one call each, because the failure this
    guards is a string in `yulon.commands` that is right for three trees and
    silently wrong for the fourth -- and "silently" is the word: the tortoise
    console answers `There is no such subcommand` and prints the list of the
    subcommands it does have, which reads like the app being broken.
    """
    tortoise_channel, wotlk_channel = _Channel(), _Channel()

    _tortoise(tmp_path, tortoise_channel).rename("guglu")
    _install(tmp_path, channel=wotlk_channel).rename("guglu")

    assert tortoise_channel.sent == ["rename Guglu"]
    assert wotlk_channel.sent == ["character rename Guglu"]


def test_a_tree_with_no_level_command_refuses_the_press_and_sends_nothing(tmp_path) -> None:
    """The headline of 8.4d, at the seam under the button.

    The whole command implementation of this fork writes a level in three places
    (`Commands.cpp:3516`, `:3569`, `:3826`), and the only one that reaches an
    arbitrary level is `HandleCharacterLevel`, whose single caller is
    `HandleLevelUpCommand` -- registered with `AllowConsole` FALSE
    (`Chat.cpp:923`). `CliHandler::isAvailable` refuses on that field before it
    looks at security at all (`Chat.cpp:3723-3731`), and the `command` DB table
    can override a row's SecurityLevel and Help but not its AllowConsole
    (`Chat.cpp:1730-1770`) -- so this is not a permission this install could be
    configured into.

    The tab does not draw the control, but the seam refuses on its own: a press
    that arrived anyway must not fall through to a sibling's `character level`,
    which on this fork is an unknown subcommand today and, on a fork that later
    gains one, would be a real command nobody has measured.
    """
    channel = _Channel()

    outcome = _tortoise(tmp_path, channel).set_level("guglu", 60)

    assert outcome.done is False
    assert channel.sent == [], channel.sent
    assert "reset a logged-in character" in outcome.problem, outcome.problem


def test_the_tortoise_block_carries_this_forks_own_numbers_and_not_a_siblings() -> None:
    """Every field, against the source line it was read from, in one breath.

    The hazard 8.4c named is the same one here: Vanilla's block matches this one
    on all four of ITS fields, so pasting it over would have produced a block
    that passes every other test in this file. What would not have come with it
    are the rename verb and the absent level command, which are the two facts
    this fork disagrees with all three siblings about.
    """
    block = TORTOISE.play

    assert block is not None
    assert block.mail_item_cap == 1, "src/game/Mail/Mail.h:51 -- #define MAX_MAIL_ITEMS 1"
    assert block.teleport_command == "tele name", "Chat.cpp:716/:869 -- `teleport` is no command"
    assert block.equipped.instance_table is None, "character_inventory carries item_template"
    assert block.equipped.template_column == "item_template"
    assert block.revive_offline is True, (
        "MEASURED, not read off Commands.cpp:3040: 8.4d on m910q, 2026-09-08 -- Bramerm's "
        "corpse row held for 12 s of watching and went to 0 on the press (transcript.txt:196-204, "
        "07:37Z), and Ramoni's again in round 2 (transcript-round2.txt:48, 08:25Z). This field "
        "IS read: controller_view offers the Revive button only on True, and for a few hours on "
        "2026-09-08 it was null here -- set from a belief that no box had pressed it, while the "
        "press sat unmerged in a stopped workflow's worktree -- which took the button off a tab "
        "where it had already worked"
    )
    assert block.rename_command == "rename", "Chat.cpp:850 -- top level, not under `character`"
    assert block.set_level_command is None, "Chat.cpp:923 -- .levelup is AllowConsole=false"


def test_the_sentence_in_place_of_the_level_control_names_what_this_fork_has() -> None:
    """ "a sentence naming what does exist rather than one implying nothing does".

    Re-measured on this fork's own source, 2026-09-08, after the first version
    of this sentence said "its console has no command that puts a character at a
    level you pick" and that was FALSE. The first measurement read
    `src/game/Chat/Chat.cpp` and `Commands.cpp` and stopped there; `.rndbot` is
    registered `AllowConsole=true` at `Chat.cpp:1012`, and a console `.rndbot
    create level=<n>` reaches `PlayerbotHolder::HandleCreate`
    (`modules/mod-playerbots/src/playerbot/PlayerbotMgr.cpp:2593`, no security
    check and no master required, unlike `HandleGroup` below it) and then
    `CreateBot` (`:2350`), which parses `level=` at `:2389` and runs
    `SetLevel(level)` at `:2498`.

    So the clause that survives is narrower, and every word of it was read here:

    * nothing moves an EXISTING character to a chosen level. `.levelup` is
      `AllowConsole=false` (`Chat.cpp:923`), and the playerbot table's own
      `level`/`levelup` verb (`PlayerbotMgr.cpp:336`) is
      `HandleBotLevelUp` (`:3159`), whose whole body is
      `PlayerbotFactory factory(bot, bot->GetLevel()); factory.Randomize(...)` --
      it never reads the parameter it was handed. `init` (`:3094`) does the same
      thing: with no master, which is every console call, the level it builds
      with is `bot->GetLevel()`.
    * `.reset level` IS console-legal (`Chat.cpp:653`, `AllowConsole=true`), its
      handler takes only a `Player**` through `ExtractPlayerTarget` so the
      character has to be logged in, and the level it writes is
      `CONFIG_UINT32_START_PLAYER_LEVEL` (`Commands.cpp:3824-3826`).
    * `.rndbot create level=<n>` makes a NEW character at the level named.

    The number that value happens to hold is NOT in the sentence: it is
    `StartPlayerLevel` in the operator's own `etc/mangosd.conf`, editable on any
    install, and this catalog is shipped data that never re-reads it.
    """
    said = TORTOISE.play.set_level_absent_reason

    assert said
    # T194 (I2): the sentence is the player's. The console commands and why each
    # was chosen stay in this docstring; the screen says what each one does.
    assert "`" not in said and ".rndbot" not in said and ".levelup" not in said, said
    assert "can't move a character to a level you pick" in said, said
    assert "reset a logged-in character to the starting level" in said, said
    # Finding 5: a conf key's current value is not a fact this file may ship.
    assert "level 1" not in said, said
    # Finding 1: the console route that DOES take a level is named, and named as
    # what it is -- a new character rather than a change to an existing one.
    assert "make a new bot at the level you name" in said, said


def test_the_offline_rename_this_fork_would_destroy_a_name_with_is_refused(tmp_path) -> None:
    """`rename <char>` with no new name is the at-login flag only for a character
    who is ONLINE.

    For an offline one the same spelling runs
    `UPDATE characters SET name = guid, at_login = at_login|1`
    (`src/game/Commands/Commands.cpp:12624-12635`) -- it does not flag the
    rename, it throws the current name away and puts the numeric guid there.
    That is data loss behind a button labelled "Rename at next login", so the
    entry carries the refusal and this asserts the tree that has one and a tree
    that has not, in one breath.
    """
    said = TORTOISE.play.rename_offline_refusal

    assert said
    assert "logged in" in said, said
    assert "guid" in said, "the sentence names what the server would do instead"
    assert WOTLK.play is not None and WOTLK.play.rename_offline_refusal is None


# -- 8.4d review, finding 2: the destructive action gets the seam guard -------


def _one_row(tmp_path, entry, row: str, channel):
    """An install whose character lookup answers `row`, verbatim, and its reader.

    Keyed on `UPPER(name)` rather than on the `SELECT name FROM` prefix the rest
    of this file uses, because the read this guard needs comes back with the
    online column BESIDE the name -- a fixture keyed on the old prefix would
    stop matching the moment that select grows, and a test asserting "nothing
    was sent" would then pass because the name resolved to nothing rather than
    because the guard fired.
    """
    reader = _Reader(**{"UPPER(name)": row})
    install = play.InstallPlay(entry, tmp_path, sql=reader, channel_for_saved=lambda: channel)
    return install, reader


def test_a_rename_is_not_sent_to_a_character_the_database_says_is_offline(tmp_path) -> None:
    """The seam refuses it, not only the button.

    The button was the whole protection until now, and it is driven by the
    `online` flag of a character-list SNAPSHOT: the list is read once, and a bot
    or a player that logs out a second later leaves a row that still says 1.
    `RandomPlayerbotMgr` cycles its bots on a timer, so on the tree this matters
    on, the snapshot goes stale on its own with nobody touching anything.

    What is behind the button there is not an ineffective command, it is a
    DIFFERENT one: `rename <char>` on an offline character runs
    `UPDATE characters SET name = guid, at_login = at_login | '1'`
    (`src/game/Commands/Commands.cpp:12624-12635`, re-read on m910q 2026-09-08),
    which throws the name away. `set_level`, whose worst outcome is a harmless
    `There is no such subcommand`, already had the belt-and-braces guard; this
    is the action that needed it.
    """
    channel = _Channel()
    install, _ = _one_row(tmp_path, TORTOISE, "Ganaar\t0\n", channel)

    outcome = install.rename("ganaar")

    assert outcome.done is False
    assert channel.sent == [], channel.sent
    assert outcome.problem.startswith("Ganaar "), outcome.problem
    assert "logged in" in outcome.problem, outcome.problem
    assert "guid" in outcome.problem, outcome.problem


def test_the_rename_guard_reads_the_row_at_the_press_rather_than_the_list(tmp_path) -> None:
    """The reading the refusal is made from is taken WHEN THE BUTTON IS PRESSED.

    Two assertions, because either alone is satisfied by a guard that is not
    really asking: the statement the seam sent NAMES the online column, and the
    same install answers differently for the same character when the row behind
    it says 1 rather than 0. A guard reading a snapshot taken at construction
    would answer the same both times, and one that hard-coded "offline" would
    never send at all.
    """
    refused_channel, sent_channel = _Channel(), _Channel()
    offline, reader = _one_row(tmp_path, TORTOISE, "Guglu\t0\n", refused_channel)
    online, _ = _one_row(tmp_path, TORTOISE, "Guglu\t1\n", sent_channel)

    refused = offline.rename("guglu")
    allowed = online.rename("guglu")

    assert "online" in reader.asked[0], reader.asked[0]
    assert refused.done is False
    assert refused_channel.sent == [], refused_channel.sent
    assert allowed.done is True, allowed.problem
    assert sent_channel.sent == ["rename Guglu"], sent_channel.sent


def test_a_tree_that_measured_no_such_hazard_still_flags_an_offline_rename(tmp_path) -> None:
    """The guard is the ENTRY's fact and not a blanket rule.

    WotLK's own box watched `character rename` on an offline character do
    exactly what its name says -- set the at-login flag and nothing else -- so
    its entry carries no refusal and this must keep sending. A guard that
    refused every offline rename everywhere would pass the test above and take a
    working action away from three trees.
    """
    channel = _Channel()
    install, _ = _one_row(tmp_path, WOTLK, "Guglu\t0\n", channel)

    outcome = install.rename("guglu")

    assert outcome.done is True, outcome.problem
    assert channel.sent == ["character rename Guglu"], channel.sent


def test_the_actions_that_are_safe_offline_are_not_caught_by_the_rename_guard(
    tmp_path,
) -> None:
    """Every other action on this fork works on a character who is out.

    The teleport's own help says so in as many words, and 8.4c watched the
    offline revive remove a corpse. A guard written into the shared `_one`
    rather than into `rename` alone would have taken all of them out on this
    tree, and nothing else in this file presses them against an offline row.
    """
    channel = _Channel()
    install, _ = _one_row(tmp_path, TORTOISE, "Ganaar\t0\n", channel)

    assert install.teleport("ganaar", "Stormwind").done is True
    assert install.revive("ganaar").done is True
    assert channel.sent == ["tele name Ganaar Stormwind", "revive Ganaar"], channel.sent


def test_the_tortoise_tab_is_handed_a_seam_built_from_this_forks_own_entry(tmp_path) -> None:
    """The line 8.4d was missing, and every test above it passed without.

    The catalog block, the commands and the tab were all written for this tree
    and `_for_tortoise` never bound the seam, so `ControllerServices.play` was
    None and the Characters tab drew *"WoW Tortoise has not had its character
    actions measured yet"* -- the entry saying the measurement exists and the
    window saying it does not. `test_every_game_offers_the_whole_controller_
    surface_wotlk_does` is the guard that caught it, and it asserts presence.

    This asserts WHICH, which is the half that file's own docstring warns
    presence is not: a seam handed a sibling's entry would send
    `character rename` on the one fork where that is not a command, and would
    promise twelve items per mail on the one that carries one. The entry is the
    object every one of those facts is read from, so naming it names them all.
    """
    from yulon.ui.controller_view import ControllerServices

    server_dir = tmp_path / TORTOISE.id
    server_dir.mkdir()
    password_file = TORTOISE.install.password.file
    if password_file:
        (server_dir / password_file).write_text("hunter2", encoding="utf-8")

    seam = ControllerServices.for_entry(TORTOISE, server_dir).play

    assert isinstance(seam, play.InstallPlay)
    assert seam.entry.id == "wow-tortoise", seam.entry.id
    assert seam.entry.play is not None
    assert seam.entry.play.rename_command == "rename"
    assert seam.entry.play.set_level_command is None
    assert seam.mail_item_cap == 1


# -- T584: a level on an online character is saved before it is read back ----


class _LevelRows:
    """A reader that knows the name lookup and walks `characters.level` through a script."""

    def __init__(self, online: str, levels: list[str]) -> None:
        self.online = online
        self.levels = list(levels)
        self.level_reads = 0

    def query(self, db: str, statement: str) -> str:
        if "SELECT name, online FROM" in statement:
            return f"Guglu\t{self.online}\n"
        if "SELECT level FROM" in statement:
            self.level_reads += 1
            return (self.levels.pop(0) if len(self.levels) > 1 else self.levels[0]) + "\n"
        return ""

    def run_statement(self, db: str, statement: str) -> None:  # pragma: no cover
        raise AssertionError("a read must not write")


def test_a_level_on_an_online_character_is_saved_and_read_back_until_the_row_agrees(
    tmp_path,
) -> None:
    """Measured on Unbound, 500 bots online: `.character level` writes no row for a character in
    the world, the row stayed at the old level for 125 s, and one `.saveall` made it read the new
    one 2.5 to 4.7 s later. The tab sent no save, so it showed the old level."""
    reader = _LevelRows("1", ["12", "12", "70"])
    channel = _Channel(text="You changed level of Guglu to 70.")
    naps: list[float] = []
    install = _install(tmp_path, sql=reader, channel=channel)

    outcome = install.set_level_and_save("guglu", 70, sleep=naps.append)

    assert channel.sent == ["character level Guglu 70", "saveall"]
    assert outcome.done is True
    assert outcome.text == "You changed level of Guglu to 70."
    assert reader.level_reads == 3
    assert len(naps) == 2, "it waits between reads and not after the one that agrees"


def test_a_row_that_never_agrees_ends_in_a_plain_sentence_after_a_bounded_wait(tmp_path) -> None:
    reader = _LevelRows("1", ["12"])
    naps: list[float] = []
    install = _install(tmp_path, sql=reader, channel=_Channel(text="Level changed."))

    outcome = install.set_level_and_save("guglu", 70, tries=4, pause=0.5, sleep=naps.append)

    assert outcome.done is True, "the server did take the level"
    assert outcome.text.startswith("Level changed.")
    assert "has not written" in outcome.text and "12" in outcome.text
    assert reader.level_reads == 4
    assert naps == [0.5, 0.5, 0.5]


def test_an_offline_character_needs_no_save_and_is_not_polled(tmp_path) -> None:
    """The offline arm of `.character level` writes the row itself."""
    reader = _LevelRows("0", ["12"])
    channel = _Channel()
    install = _install(tmp_path, sql=reader, channel=channel)

    outcome = install.set_level_and_save("guglu", 70, sleep=lambda _: None)

    assert outcome.done is True
    assert channel.sent == ["character level Guglu 70"]
    assert reader.level_reads == 0


def test_a_refused_level_is_not_followed_by_a_save(tmp_path) -> None:
    reader = _LevelRows("1", ["12"])
    channel = _Channel("no")
    install = _install(tmp_path, sql=reader, channel=channel)

    outcome = install.set_level_and_save("guglu", 70, sleep=lambda _: None)

    assert outcome.done is False
    assert channel.sent == ["character level Guglu 70"]
    assert reader.level_reads == 0


def test_a_save_the_server_refuses_is_said_and_the_row_is_not_polled(tmp_path) -> None:
    reader = _LevelRows("1", ["12"])
    channel = _Channel("yes", "no")
    install = _install(tmp_path, sql=reader, channel=channel)

    outcome = install.set_level_and_save("guglu", 70, sleep=lambda _: None)

    assert outcome.done is True, "the level itself was taken"
    assert "the server said no" in outcome.text
    assert reader.level_reads == 0
