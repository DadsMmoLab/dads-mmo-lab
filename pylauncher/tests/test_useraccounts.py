"""The account list the Accounts tab shows (8.3a).

A read, and what it leaves OUT is the whole point. A WotLK install with the
owner's settings has 500 bot accounts and one account this app made for itself;
a list that shows all 502 rows is a list nobody can use, and one of those rows
is a credential the user must never be handed a Set-password button for.
"""

from __future__ import annotations

import pytest

from yulon import dbreads, useraccounts
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")
MARKER = dbreads.Marker(prefix="rndbot", source="default")


class _Reader:
    """A SQL seam that answers one statement and records what it was asked."""

    def __init__(self, answer: str = "") -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    def query(self, db: str, statement: str) -> str:
        self.asked.append((db, statement))
        return self.answer

    def run_statement(self, db: str, statement: str) -> None:  # pragma: no cover
        raise AssertionError("the list must not write")


def test_the_list_is_the_rows_the_server_has() -> None:
    sql = _Reader("1\tALICE\t0\n7\tBOB\t3\n")

    listing = useraccounts.accounts(sql, WOTLK, MARKER, app_account="YULON_AB12CD34")

    assert [(a.id, a.username, a.gm_level) for a in listing.accounts] == [
        (1, "ALICE", 0),
        (7, "BOB", 3),
    ]
    assert listing.problem == ""


def test_the_bots_are_left_out_by_this_installs_own_marker() -> None:
    """500 of them, and the marker is the live value 8.1a resolves."""
    sql = _Reader("")

    useraccounts.accounts(sql, WOTLK, MARKER, app_account="YULON_AB12CD34")

    statement = sql.asked[0][1]
    assert "RNDBOT%" in statement.upper()
    assert "NOT (" in statement


def test_the_apps_own_account_is_left_out_because_it_is_not_the_users_to_change() -> None:
    """It is in the list's WHERE, not filtered afterwards.

    Filtering after the read would leave the row on the wire and in any log of
    the statement, and would put the burden on every future caller to remember.
    """
    sql = _Reader("")

    useraccounts.accounts(sql, WOTLK, MARKER, app_account="YULON_AB12CD34")

    # By the prefix every account this app makes carries, not by this install's
    # own name: two installs can share an auth database, and the neighbour's
    # channel account must not be listed either.
    assert "LEFT(a.username, 6) <> 'YULON_'" in sql.asked[0][1]


def test_the_auction_house_bot_account_is_not_listed_as_a_person() -> None:
    """`AHBOT` is a service account the module's own manifest tells the user to create.

    rust-main excluded it (`crates/dml-wow/src/pages.rs:302`), and this port
    dropped it -- so the accounts tab listed it beside real people and offered
    to change its password, which would have silently broken the auction house
    (retrospective audit, 2026-09-08: the concrete cost of building this feature
    without reading the prior art first).
    """
    sql = _Reader("")
    useraccounts.accounts(sql, WOTLK, MARKER, app_account="YULON_AB12CD34")

    assert "a.username <> 'AHBOT'" in sql.asked[0][1]


def test_a_row_the_read_cannot_parse_is_a_problem_rather_than_a_silent_gap() -> None:
    sql = _Reader("1\tALICE\t0\nnonsense\n")

    listing = useraccounts.accounts(sql, WOTLK, MARKER, app_account="YULON_AB")

    assert listing.accounts == []
    assert "nonsense" in listing.problem


def test_a_database_that_cannot_be_read_says_so_rather_than_showing_no_accounts() -> None:
    """An empty list and an unreadable database look identical on screen.

    One of them means "this server has no accounts" and the other means "do not
    trust what you are looking at", and the tab has to be able to tell a person
    which.
    """

    class _Broken(_Reader):
        def query(self, db: str, statement: str) -> str:
            raise RuntimeError("the database container is not running")

    listing = useraccounts.accounts(_Broken(), WOTLK, MARKER, app_account="YULON_AB")

    assert listing.accounts == []
    assert "not running" in listing.problem


def test_the_gm_level_comes_from_where_this_tree_keeps_it() -> None:
    """A per-tree fact, in the catalog, measured on this install.

    AzerothCore keeps the level in `account_access` keyed `id`; the CMaNGOS
    trees keep it on the account row. Reading the wrong one shows every account
    as level 0, which is a lie that looks like an answer.
    """
    sql = _Reader("")

    useraccounts.accounts(sql, WOTLK, MARKER, app_account="YULON_AB")

    statement = sql.asked[0][1]
    assert "account_access" in statement
    assert "gmlevel" in statement


def test_a_game_whose_level_store_has_not_been_measured_refuses_to_guess() -> None:
    entry = load_catalog().get("wow-tbc")
    if entry.accounts.level is not None:  # pragma: no cover - it has been measured since
        pytest.skip("wow-tbc's level store has been measured")

    listing = useraccounts.accounts(_Reader(), entry, MARKER, app_account="YULON_AB")

    assert listing.accounts == []
    assert entry.name in listing.problem


# -- the two actions, through the server's own commands ---------------------


class _Channel:
    """A channel that records the command text and answers from a script."""

    def __init__(
        self,
        *outcomes: str,
        reason: str = "",
        indeterminate: bool = False,
    ) -> None:
        self.outcomes = list(outcomes) or ["yes"]
        self.sent: list[str] = []
        self.reason = reason
        self.indeterminate = indeterminate

    def send(self, command: str) -> object:
        self.sent.append(command)
        outcome = self.outcomes.pop(0) if self.outcomes else "yes"
        default_reason = "" if outcome != "unknown" else "the server could not be reached"
        return type(
            "Answer",
            (),
            {
                "outcome": outcome,
                "text": "done" if outcome == "yes" else "the server said no",
                "reason": self.reason or default_reason,
                "indeterminate": self.indeterminate,
                "denied": False,
            },
        )()


def test_a_password_change_is_the_servers_own_command_and_not_a_row_written_here() -> None:
    """Owner answer 7: the server performs its own writes.

    A password written as a row is a row this app has to get exactly right on
    every core, forever. The server already knows how; it is asked.
    """
    channel = _Channel()

    outcome = useraccounts.set_password(
        channel, account="ALICE", password="n3w-p@ss", app_account="YULON_AB", digits_are_ids=False
    )

    assert outcome.done is True
    assert channel.sent == ["account set password ALICE n3w-p@ss n3w-p@ss"]


def test_a_level_change_is_the_servers_own_command_across_every_realm() -> None:
    channel = _Channel()

    outcome = useraccounts.set_gm_level(
        channel,
        account="ALICE",
        level=2,
        app_account="YULON_AB",
        realms=True,
        highest=3,
        digits_are_ids=False,
    )

    assert outcome.done is True
    assert channel.sent == ["account set gmlevel ALICE 2 -1"]


def test_neither_action_may_be_taken_on_the_apps_own_account() -> None:
    """The clause the box names, enforced where the command is built.

    Not in the UI, where it would be one forgotten `if` away from being gone:
    changing this account's password breaks the channel every other Phase 8
    feature rides on, and changing its level below 3 breaks it just as
    thoroughly.
    """
    for act in (
        lambda c: useraccounts.set_password(
            c,
            account="yulon_ab",
            password="whatever1",
            app_account="YULON_AB",
            digits_are_ids=False,
        ),
        lambda c: useraccounts.set_gm_level(
            c,
            account="YULON_AB",
            level=0,
            app_account="YULON_AB",
            realms=True,
            highest=3,
            digits_are_ids=False,
        ),
    ):
        channel = _Channel()

        outcome = act(channel)

        assert outcome.done is False
        assert "its own" in outcome.problem
        assert channel.sent == [], "it sent the command before refusing"


def test_an_argument_the_server_would_refuse_is_refused_here_instead() -> None:
    channel = _Channel()

    outcome = useraccounts.set_password(
        channel, account="ALICE", password="no", app_account="YULON_AB", digits_are_ids=False
    )

    assert outcome.done is False
    assert channel.sent == []


def test_a_server_that_says_no_is_reported_in_the_servers_own_words() -> None:
    outcome = useraccounts.set_password(
        _Channel("no"),
        account="ALICE",
        password="n3w-p@ss",
        app_account="YULON_AB",
        digits_are_ids=False,
    )

    assert outcome.done is False
    assert "the server said no" in outcome.problem


def test_a_channel_that_could_not_ask_says_that_rather_than_that_it_failed() -> None:
    """Three outcomes, not two: "could not ask" is its own answer.

    Reporting an unreachable server as a failed change would have the user
    believe the password is unchanged when nobody knows whether it is.
    """
    outcome = useraccounts.set_gm_level(
        _Channel("unknown"),
        account="ALICE",
        level=1,
        app_account="YULON_AB",
        realms=True,
        highest=3,
        digits_are_ids=False,
    )

    assert outcome.done is False
    assert "could not be reached" in outcome.problem


# -- the object the tab holds ------------------------------------------------


def _install(tmp_path, *, sql, channel=None):
    return useraccounts.InstallAccounts(
        WOTLK,
        tmp_path,
        sql=sql,
        channel_for_saved=lambda: channel,
        app_account="YULON_AB12CD34",
    )


def test_the_listing_uses_this_installs_own_marker(tmp_path) -> None:
    """Read from the install, not from the catalog's default.

    The owner's servers run 500 bots under whatever prefix that install's conf
    says; a listing built on the shipped default would show every one of them
    the moment somebody changed it.
    """
    conf = tmp_path / WOTLK.observability.bots.prefix_conf_file
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("AiPlayerbot.RandomBotAccountPrefix = HOUSEBOT\n", encoding="utf-8")
    sql = _Reader("")

    _install(tmp_path, sql=sql).listing()

    assert "HOUSEBOT%" in sql.asked[0][1].upper()


def test_an_action_with_no_channel_says_where_to_turn_it_on(tmp_path) -> None:
    """Rather than a failure that reads like the server's.

    These changes are made by the server, so with no channel there is nothing
    to ask — and the user needs the one sentence that gets them unstuck, not a
    connection error.
    """
    outcome = _install(tmp_path, sql=_Reader()).set_password("ALICE", "n3w-p@ss")

    assert outcome.done is False
    assert "Server tab" in outcome.problem


def test_the_install_object_refuses_its_own_account_too(tmp_path) -> None:
    channel = _Channel()

    outcome = _install(tmp_path, sql=_Reader(), channel=channel).set_gm_level("yulon_ab12cd34", 0)

    assert outcome.done is False
    assert channel.sent == []


# -- what the second adversarial review found (2026-09-07) -------------------


def test_every_account_this_app_owns_is_protected_and_not_only_this_installs() -> None:
    """Two installs can share one auth database, and then one breaks the other.

    `YULON_AAAAAAAA` and `YULON_BBBBBBBB` are two installs' channel accounts. A
    guard that knows only its own name lists the neighbour's and offers to
    change its password, which ends that install's command channel while saying
    nothing about it. The prefix is the app's, not the install's, and it is what
    the guard reads.
    """
    channel = _Channel()

    outcome = useraccounts.set_password(
        channel,
        account="YULON_BBBBBBBB",
        password="n3w-p@ss",
        app_account="YULON_AAAAAAAA",
        digits_are_ids=False,
    )

    assert outcome.done is False
    assert channel.sent == []


def test_an_ordinary_account_that_merely_starts_with_the_letters_is_not_protected() -> None:
    """`YULONGATE` is a person's account on the gate box, and it is theirs to change."""
    channel = _Channel()

    outcome = useraccounts.set_password(
        channel,
        account="YULONGATE",
        password="n3w-p@ss",
        app_account="YULON_AAAAAAAA",
        digits_are_ids=False,
    )

    assert outcome.done is True
    assert channel.sent == ["account set password YULONGATE n3w-p@ss n3w-p@ss"]


def test_the_list_leaves_out_every_account_this_app_owns() -> None:
    sql = _Reader("")

    useraccounts.accounts(sql, WOTLK, MARKER, app_account="YULON_AB12CD34")

    statement = sql.asked[0][1]
    assert "LEFT(a.username, 6) <> 'YULON_'" in statement


def test_a_change_the_server_did_not_answer_in_time_is_not_reported_as_a_failure() -> None:
    """A timeout means the command MAY have run.

    The SOAP listener queues onto the world thread and blocks until the command
    finishes, so a client giving up says nothing about whether the server did.
    Told "that did not work", a person retypes the old password and is locked
    out of an account whose password has already changed.
    """

    class _Slow:
        sent: list[str] = []

        def send(self, command: str) -> object:
            self.sent.append(command)
            return type(
                "Answer",
                (),
                {
                    "outcome": "unknown",
                    "text": "",
                    "reason": "the server did not answer within 20s",
                    "indeterminate": True,
                    "denied": False,
                },
            )()

    outcome = useraccounts.set_password(
        _Slow(), account="ALICE", password="n3w-p@ss", app_account="YULON_AB", digits_are_ids=False
    )

    assert outcome.done is False
    assert outcome.indeterminate is True
    assert "may already" in outcome.problem.lower()


# -- 8.3b: the reply is a hint; the ROW is the answer, and only if it is ours --


class _Row:
    """A stand-in for the reader that asks whether a password is now in force."""

    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, str]] = []

    def __call__(self, account: str, password: str) -> bool:
        self.asked.append((account, password))
        return self.answers.pop(0) if self.answers else False


def test_a_password_change_this_core_reports_as_failed_is_confirmed_by_the_row() -> None:
    """Measured on m910q, 2026-09-07, and it is by design in the core.

    CMaNGOS's `HandleAccountSetPasswordCommand` sends its success message and
    then `SetSentErrorMessage(true); return false;` -- deliberately, "to avoid
    normal report for hide passwords" (`Level3.cpp:1178-1183`). SOAP turns a
    handler that returned false into a fault, so a SUCCESSFUL change comes back
    as a failure; on the live TBC server the salt and verifier both moved while
    the app was told the channel was unreachable.

    Telling a person their password did not change when it did is the worst of
    the available wrongs: they retype the old one, for an account that no longer
    has it. 8.3a's own review raised this hazard for timeouts; here it is
    guaranteed rather than occasional.

    So the row decides -- but only by answering the one question that belongs to
    THIS command: is the password we were asked to set the one the account now
    has? See `test_a_row_that_changed_to_somebody_elses_password_is_not_a_yes`.
    """
    row = _Row(True)

    outcome = useraccounts.set_password(
        _Channel("unknown"),
        account="ALICE",
        password="n3w-p@ss34",
        app_account="YULON_AB",
        password_is_in_force=row,
        digits_are_ids=False,
    )

    assert outcome.done is True, outcome.problem
    assert "changed" in outcome.text.lower(), outcome.text
    assert row.asked == [("ALICE", "n3w-p@ss34")], "it asks about ITS OWN password"


def test_a_row_that_changed_to_somebody_elses_password_is_not_a_yes() -> None:
    """8.3b's adversarial review, and it was right.

    An earlier version of this took any change to the credential columns as
    proof that this command had made it. Anything else that writes the row --
    a second window, an administrator at a console, another install sharing the
    auth database -- would then have this app tell a person their new password
    works while the account holds a different one. That person is now locked
    out BY the reassurance, which is worse than the failure it replaced.

    The reader answers the narrow question instead, and a no is a no.
    """
    row = _Row(False)

    outcome = useraccounts.set_password(
        _Channel("unknown"),
        account="ALICE",
        password="n3w-p@ss34",
        app_account="YULON_AB",
        password_is_in_force=row,
        digits_are_ids=False,
    )

    assert outcome.done is False
    assert outcome.problem, "a failure with no sentence is not an answer"


def test_a_yes_the_row_contradicts_is_not_a_yes() -> None:
    """Measured on the live Tortoise server, 2026-09-07, and it is a lockout.

    That fork's `account set password` answers "The password was changed" and
    then stores a hash the password cannot reproduce: for an account that has
    logged in before, the name is EMPTY in the hash it writes.

        stored   A78031B82173E3D5AB216AB0835A165DB5D56020
        SHA1(":PR0BE-P@SS55")                     matches
        SHA1("GATE83E:PR0BE-P@SS55")              does not

    The account can then never log in — measured, with a real 1.18.1 client —
    and the server has just told this app it succeeded. An earlier version of
    this test asserted that a clean yes is believed WITHOUT reading anything;
    the machine refuted it, so this is the same test rewritten around what
    happens rather than around what ought to.

    One SELECT per password change is the price, and it buys the difference
    between a person who knows their new password did not take and a person
    locked out of their account by a reassurance.
    """
    row = _Row(False)

    outcome = useraccounts.set_password(
        _Channel("yes"),
        account="ALICE",
        password="n3w-p@ss34",
        app_account="YULON_AB",
        password_is_in_force=row,
        digits_are_ids=False,
    )

    assert outcome.done is False, "the server said yes and the row says otherwise"
    assert "will not" in outcome.problem or "not that password" in outcome.problem, outcome.problem
    assert row.asked == [("ALICE", "n3w-p@ss34")]


def test_a_yes_the_row_agrees_with_is_a_yes() -> None:
    """The other side of it, so the check cannot be satisfied by refusing."""
    outcome = useraccounts.set_password(
        _Channel("yes"),
        account="ALICE",
        password="n3w-p@ss34",
        app_account="YULON_AB",
        password_is_in_force=_Row(True),
        digits_are_ids=False,
    )

    assert outcome.done is True


def test_a_yes_stands_where_the_row_cannot_be_read_at_all() -> None:
    """`None` is not `False`: a tree with no measured recipe, or a database that
    did not answer, leaves the server's own reply exactly as it was."""
    outcome = useraccounts.set_password(
        _Channel("yes"),
        account="ALICE",
        password="n3w-p@ss34",
        app_account="YULON_AB",
        password_is_in_force=lambda account, password: None,
        digits_are_ids=False,
    )

    assert outcome.done is True


def test_a_reader_that_throws_leaves_the_servers_own_answer_standing() -> None:
    """The database is one more thing that can be down, and it is not this
    command's failure to report. What the server said stands."""

    def broken(account: str, password: str) -> bool:
        raise RuntimeError("the database is not answering")

    outcome = useraccounts.set_password(
        _Channel("unknown"),
        account="ALICE",
        password="n3w-p@ss34",
        app_account="YULON_AB",
        password_is_in_force=broken,
        digits_are_ids=False,
    )

    assert outcome.done is False
    assert "not answering" not in outcome.problem, "the person is told about the command"


def test_a_password_change_with_no_reader_falls_back_to_the_reply() -> None:
    """`password_is_in_force` is optional, and without it the reply is all there is.

    Kept optional so a caller with no database seam -- a test, a tree whose
    scheme nobody has measured -- gets the old behaviour rather than an
    exception.
    """
    outcome = useraccounts.set_password(
        _Channel("yes"),
        account="ALICE",
        password="n3w-p@ss34",
        app_account="YULON_AB",
        digits_are_ids=False,
    )

    assert outcome.done is True


def test_an_indeterminate_answer_does_not_invent_the_reason_it_is_indeterminate() -> None:
    """Two different machines end here, and only one of them is a timeout.

    A timeout is this app giving up while the server works on. A CMaNGOS
    refusal is the opposite: the server hung up on us, immediately (8.3b,
    measured at the wire). Telling a person "the server keeps working on a
    command after this app stops waiting" for the second one describes
    something that did not happen, and the reason the channel supplies already
    says what did.
    """
    outcome = useraccounts.set_password(
        _Channel(
            "unknown",
            reason=(
                "the server took the command and closed the connection without answering. "
                "On this server that is also what a refused command looks like, so the "
                "command may have run."
            ),
            indeterminate=True,
        ),
        account="ALICE",
        password="n3w-p@ss34",
        app_account="YULON_AB",
        digits_are_ids=False,
    )

    assert outcome.indeterminate is True
    assert "closed the connection" in outcome.problem
    assert "stops waiting" not in outcome.problem, outcome.problem
    assert "check" in outcome.problem.lower(), "it still tells them what to do"


def test_the_salt_is_read_as_the_salt_on_a_tree_that_names_it_s(tmp_path) -> None:
    """The whole path, from the catalog's scheme to a live vector.

    Two failures live between the parts here and neither would raise. The
    CMaNGOS trees name the pair `s`, `v` and AzerothCore names it `salt`,
    `verifier` -- so a pair read in the wrong ORDER answers "no" for every
    correct password, and this app would tell a person their password change
    failed every single time on one tree. And a scheme with no measured recipe
    must answer no rather than guess.

    The vector is the one `tests/test_srp6.py` documents: account SRPPROBE,
    password "kn0wn-p@ss77", read off the live Vanilla server on m910q on
    2026-09-07.
    """
    salt = "E040A443299D8590D08C3353B3F9960548A9BB82B873659FC7533CA2031B485A"
    verifier = "27BADE411219414667B39335D71F258E191312B78C065F630954135E8870111B"
    vanilla = load_catalog().get("wow-vanilla")
    reader = _Reader(f"{salt}\t{verifier}\n")
    install = useraccounts.InstallAccounts(
        vanilla,
        tmp_path,
        sql=reader,
        channel_for_saved=lambda: None,
        app_account="YULON_AB12CD34",
    )

    assert install._password_is_in_force("SRPPROBE", "kn0wn-p@ss77") is True
    assert install._password_is_in_force("SRPPROBE", "some-other-pass") is False
    asked = reader.asked[0][1]
    assert "SELECT s, v" in asked, asked
    assert "SRPPROBE" not in asked, "names are sent as a literal, not spliced in"


def test_a_tree_whose_scheme_has_no_measured_recipe_says_it_cannot_tell(tmp_path) -> None:
    """`None`, not `False`, and the difference decides what a person is told.

    AzerothCore answers its own commands properly, so nothing there needs a row
    believed, and inventing a recipe for it would be a guess wearing a
    measurement's clothes. But since 8.3d a `False` here CONTRADICTS the
    server's own yes and reports a failure -- so a tree this module has never
    measured would have every successful password change reported as broken.
    """
    reader = _Reader("00\t00\n")
    install = _install(tmp_path, sql=reader)

    assert install._password_is_in_force("SRPPROBE", "kn0wn-p@ss77") is None
    assert reader.asked == [], "a scheme it cannot check is not worth a query"


def test_another_writer_between_the_command_and_the_read_cannot_forge_a_yes(tmp_path) -> None:
    """The review's own scenario, end to end through the object the tab holds.

    The command is refused (this core refuses by hanging up, so the channel
    answers `unknown`), and while that happens somebody else changes the same
    account's password. The row is now different from what it was — which is
    exactly what the first version of this took as proof — and it is not this
    password, so the answer is no.

    A false POSITIVE is not merely unlikely here, it is unreachable: the only
    row that says yes is a row holding the password this call was given, and
    then "the password was changed" is true whoever wrote it.
    """
    salt = "E040A443299D8590D08C3353B3F9960548A9BB82B873659FC7533CA2031B485A"
    somebody_elses = "27BADE411219414667B39335D71F258E191312B78C065F630954135E8870111B"
    channel = _Channel("unknown")
    install = useraccounts.InstallAccounts(
        load_catalog().get("wow-vanilla"),
        tmp_path,
        sql=_Reader(f"{salt}\t{somebody_elses}\n"),
        channel_for_saved=lambda: channel,
        app_account="YULON_AB12CD34",
    )

    outcome = install.set_password("SRPPROBE", "the-password-we-asked-for")

    assert outcome.done is False, "a stranger's write must not be reported as ours"
    assert outcome.problem


def test_the_refusal_does_not_claim_an_account_this_tree_never_had() -> None:
    """8.3d, read off the live Tortoise gate.

    The guard reserves a name derived from the install id, and refuses it
    whether or not an account by that name exists. On the CMaNGOS and
    AzerothCore trees this app does make one, for its SOAP channel. On the
    Tortoise fork it never does — the console IS the channel, so there is no
    credential and no account — and the sentence still said "is an account this
    app made for its own command channel", about an account nobody had made.

    A refusal that describes something that does not exist teaches a person the
    wrong thing about their own server, and this one is shown in the tab.
    """
    outcome = useraccounts.set_gm_level(
        _Channel(),
        account="YULON_AB",
        level=1,
        app_account="YULON_AB",
        realms=False,
        highest=4,
        digits_are_ids=False,
    )

    assert outcome.done is False
    assert "this app made" not in outcome.problem, outcome.problem
    assert "reserves" in outcome.problem, outcome.problem
    assert "command channel" in outcome.problem


# -- T579: a level given at Create must reach the running world --------------

TORTOISE = load_catalog().get("wow-tortoise")


def _tortoise_install(tmp_path, *, channel=None):
    return useraccounts.InstallAccounts(
        TORTOISE,
        tmp_path,
        sql=_Reader(),
        channel_for_saved=lambda: channel,
        app_account="YULON_AB12CD34",
    )


def test_an_account_created_with_a_level_is_told_to_the_running_world() -> None:
    """The world keeps each rank it read at start, so the row alone is not live (T579)."""
    channel = _Channel("yes")

    said = _tortoise_install(None, channel=channel).after_create("BOB", 3)

    assert channel.sent == ["account set gmlevel BOB 3"]
    assert "live" in said, said


def test_without_a_command_channel_the_level_is_said_to_wait_for_the_next_start() -> None:
    said = _tortoise_install(None, channel=None).after_create("BOB", 3)

    assert "next starts" in said and "GM level 3" in said, said


def test_a_channel_that_cannot_reach_the_world_says_the_same() -> None:
    channel = _Channel("unknown")

    said = _tortoise_install(None, channel=channel).after_create("BOB", 3)

    assert channel.sent == ["account set gmlevel BOB 3"]
    assert "next starts" in said, said
    assert "live" not in said, said


def test_a_new_account_with_no_level_sends_nothing() -> None:
    channel = _Channel("yes")

    assert _tortoise_install(None, channel=channel).after_create("BOB", 0) == ""
    assert channel.sent == []


def test_a_core_that_reads_the_level_per_login_is_not_sent_the_command() -> None:
    channel = _Channel("yes")

    assert _install(None, sql=_Reader(), channel=channel).after_create("BOB", 3) == ""
    assert channel.sent == []
