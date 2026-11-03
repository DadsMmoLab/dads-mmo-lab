"""Tests for `yulon.passwordcheck` — is this row the password we asked for? (8.3b)

A password change on the CMaNGOS trees reports a failure when it works, so the
row has to answer instead. "The row changed" is NOT that answer: any other
writer changes the row too, and a command that was refused would then be
reported as a success while the account holds somebody else's password. The
answer is the verifier itself — recomputed from the row's own salt and the
password that was asked for, and compared.

The vector below is not from a specification. It was taken off the live Vanilla
server on m910q, 2026-09-07, from an account created through this app with a
known password, and every candidate byte order was tried until one reproduced
the stored verifier exactly. Exactly one did.
"""

from __future__ import annotations

from yulon import passwordcheck

# measured, m910q, 2026-09-07 — account SRPPROBE, password "kn0wn-p@ss77"
SALT = "E040A443299D8590D08C3353B3F9960548A9BB82B873659FC7533CA2031B485A"
VERIFIER = "27BADE411219414667B39335D71F258E191312B78C065F630954135E8870111B"


def _srp(account: str, password: str, salt: str = SALT, stored: str = VERIFIER) -> bool:
    return passwordcheck.matches("mangos_srp6", account, password, (salt, stored))


def test_the_recipe_reproduces_a_verifier_taken_off_the_live_server() -> None:
    assert _srp("SRPPROBE", "kn0wn-p@ss77") is True


def test_a_different_password_does_not_reproduce_it() -> None:
    """Checked on the live row too: the wrong password matched no byte order."""
    assert _srp("SRPPROBE", "WRONG-p@ss77") is False


def test_the_account_name_is_part_of_it_so_another_accounts_row_cannot_pass() -> None:
    assert _srp("SOMEONEELSE", "kn0wn-p@ss77") is False


def test_the_name_and_the_password_are_folded_the_way_the_server_folds_them() -> None:
    """Both are upper-cased before hashing, which is why a lower-case name works."""
    assert _srp("srpprobe", "kn0wn-p@ss77") is True
    assert _srp("SRPPROBE", "KN0WN-P@SS77") is True


def test_a_scheme_nobody_has_measured_answers_nothing_rather_than_a_guess() -> None:
    """The whole value of this module is that it was measured on the machine.

    A scheme that has not been is one whose password changes this app cannot
    confirm — which is a smaller answer, not a wrong one. `None` rather than
    `False` since 8.3d's review: a false answers "that password will not log
    in", and this module has no business saying that about a tree it has never
    read.
    """
    pair = (SALT, VERIFIER)
    assert passwordcheck.matches("azerothcore", "SRPPROBE", "kn0wn-p@ss77", pair) is None
    assert passwordcheck.matches("", "SRPPROBE", "kn0wn-p@ss77", pair) is None


def test_a_salt_that_is_not_hex_answers_nothing_rather_than_raising() -> None:
    """A caller reads this off a database and cannot promise what comes back."""
    assert _srp("SRPPROBE", "kn0wn-p@ss77", salt="not hex") is None
    assert _srp("SRPPROBE", "kn0wn-p@ss77", salt="") is None


def test_a_row_with_the_wrong_number_of_columns_in_it_is_not_an_answer() -> None:
    """One scheme has two columns and the other has one, so the count is a fact
    about the scheme rather than about whatever came back from the database."""
    assert passwordcheck.matches("mangos_srp6", "SRPPROBE", "kn0wn-p@ss77", (VERIFIER,)) is None
    assert passwordcheck.matches("mangos_sha", "SHAPROBE", "x", (SALT, VERIFIER)) is None


# -- comparing it with what the database actually holds ----------------------


def test_a_stored_verifier_is_matched_however_the_server_padded_it() -> None:
    """The stored text is a big number's hex, and nobody promised its width.

    This vector's verifier happens to start with `27`, so its 64 characters say
    nothing about what the server writes for a verifier whose leading byte is
    small. A comparison that insisted on the exact text would answer "not
    confirmed" for a password change that had worked -- and this app's whole
    answer here rests on that comparison. So leading zeros and case are
    normalised on both sides, and the test says so rather than the code.
    """
    assert _srp("SRPPROBE", "kn0wn-p@ss77", stored=VERIFIER)
    assert _srp("SRPPROBE", "kn0wn-p@ss77", stored=VERIFIER.lower())
    assert _srp("SRPPROBE", "kn0wn-p@ss77", stored="0000" + VERIFIER)
    assert _srp("SRPPROBE", "kn0wn-p@ss77", stored=f" {VERIFIER} ")


def test_only_a_readable_credential_can_answer_no() -> None:
    """The two halves a caller must be able to tell apart.

    A well-formed verifier for the wrong password is a no. A scheme nobody has
    measured, or an empty column, is not a no -- it is a question this module
    could not ask, and since 8.3d a no contradicts the server's own success.
    """
    assert _srp("SRPPROBE", "WRONG-p@ss77") is False
    assert (
        passwordcheck.matches("azerothcore", "SRPPROBE", "kn0wn-p@ss77", (SALT, VERIFIER)) is None
    )
    assert _srp("SRPPROBE", "kn0wn-p@ss77", stored="") is None


# -- the other tree, and the other shape ------------------------------------

# measured, m910q, 2026-09-07 -- account SHAPROBE on the Tortoise fork. The
# first is what the app wrote for the password it was given; the second is what
# the SERVER wrote into the same column after `account set password`.
SHA_KNOWN = "B83C39DFF7DCC47570C17ADF657989306814F0E6"
SHA_AFTER_THE_SERVER_CHANGED_IT = "229128ADF72BFF11D4708CE859E58DAEC5CAF1E8"


def test_the_tortoise_fork_keeps_one_unsalted_hash_and_this_is_it() -> None:
    """A different shape, not a different byte order: one column, no salt.

    Both rows below are real, and the second matters more than the first: it is
    what the server itself wrote when IT changed the password, which is the only
    thing a check on this column can honestly rest on. `v` and `s` went to `0`
    in that same write, so this column is the one that decides.
    """
    assert passwordcheck.matches("mangos_sha", "SHAPROBE", "kn0wn-p@ss77", (SHA_KNOWN,))
    assert passwordcheck.matches(
        "mangos_sha", "SHAPROBE", "tru7h-p@ss99", (SHA_AFTER_THE_SERVER_CHANGED_IT,)
    )


def test_the_tortoise_hash_is_not_reproduced_by_the_wrong_password_or_name() -> None:
    assert passwordcheck.matches("mangos_sha", "SHAPROBE", "WRONG-p@ss77", (SHA_KNOWN,)) is False
    assert passwordcheck.matches("mangos_sha", "SOMEONE", "kn0wn-p@ss77", (SHA_KNOWN,)) is False


# -- the TrinityCore family's MoP line, on the same recipe (T850-C) ----------


def test_the_mop_line_keeps_the_same_one_unsalted_hash() -> None:
    """`trinitycore_sha`: one `sha_pass_hash` column, uppercase hex, no salt.

    MoP's core hashes with the same single SHA1 over the folded `name:password`
    (`AccountMgr.cpp:345-355` `CalculateShaPassHash`) printed `%02X`
    (`Util.cpp:614` `ByteArrayToHexStr`), and its `sha_pass_hash` is
    `utf8mb4_bin` (`auth.sql:19`) — the same case-sensitive comparison the
    Tortoise fork's row gets. The vectors are the Tortoise ones because the
    recipe is the recipe: what this test would catch is the two drifting.
    """
    assert (
        passwordcheck.matches("trinitycore_sha", "SHAPROBE", "kn0wn-p@ss77", (SHA_KNOWN,)) is True
    )
    assert (
        passwordcheck.matches(
            "trinitycore_sha", "SHAPROBE", "tru7h-p@ss99", (SHA_AFTER_THE_SERVER_CHANGED_IT,)
        )
        is True
    )


def test_the_mop_hash_is_not_reproduced_by_the_wrong_password_or_name() -> None:
    assert (
        passwordcheck.matches("trinitycore_sha", "SHAPROBE", "WRONG-p@ss77", (SHA_KNOWN,)) is False
    )
    assert (
        passwordcheck.matches("trinitycore_sha", "SOMEONE", "kn0wn-p@ss77", (SHA_KNOWN,)) is False
    )


def test_the_two_trees_do_not_answer_for_each_other() -> None:
    """The scheme comes from the catalog, and a wrong one must not pass.

    Not hypothetical: `wow-tortoise`'s account row carries `v` and `s` columns
    TOO, so a check that reached for the SRP6 pair on this tree would find
    something to read and would answer no forever.
    """
    # One column where two are expected is unreadable, not wrong...
    assert passwordcheck.matches("mangos_srp6", "SHAPROBE", "kn0wn-p@ss77", (SHA_KNOWN,)) is None
    # ...and a well-formed hex value of the right shape for the OTHER tree is
    # readable, and is simply not this password.
    assert passwordcheck.matches("mangos_sha", "SRPPROBE", "kn0wn-p@ss77", (VERIFIER,)) is False


# -- 8.3d's review: a value this module cannot read is not a wrong password ---


def test_a_credential_this_module_cannot_read_is_not_a_mismatch() -> None:
    """The adversarial review's finding, and it is the dangerous direction.

    Since 8.3d a `False` here CONTRADICTS the server's own success and tells a
    person their password "will not log in". So every shape this module cannot
    evaluate — a salt that is not hex, an empty stored value, a row with the
    wrong number of columns, a scheme nobody has measured — has to answer "I
    cannot say" rather than "wrong". Storage drift in a core we do not control
    would otherwise turn every successful password change into a reported
    lockout.

    `None` and `False` are both falsy, so this test asserts identity: `is None`
    would pass for `False` under a truthiness check, and that is exactly the
    bug it is guarding.
    """
    good = (SALT, VERIFIER)
    assert passwordcheck.matches("mangos_srp6", "SRPPROBE", "kn0wn-p@ss77", good) is True

    cannot_say = [
        ("mangos_srp6", ("not hex", VERIFIER)),
        ("mangos_srp6", ("", VERIFIER)),
        ("mangos_srp6", (SALT, "")),
        ("mangos_srp6", (SALT, "not hex either")),
        ("mangos_srp6", (VERIFIER,)),
        ("mangos_srp6", (SALT, VERIFIER, "a third column")),
        ("mangos_sha", ("",)),
        ("mangos_sha", ("zzzz",)),
        ("trinitycore_sha", ("",)),
        ("trinitycore_sha", ("zzzz",)),
        ("trinitycore_sha", (SHA_KNOWN, "a second column")),
        ("azerothcore", good),
        ("", good),
    ]
    for scheme, values in cannot_say:
        assert (
            passwordcheck.matches(scheme, "SRPPROBE", "kn0wn-p@ss77", values) is None
        ), f"{scheme} {values} should be unreadable, not wrong"


def test_a_readable_credential_that_is_the_wrong_password_is_still_false() -> None:
    """The other side: the check must not answer `None` to everything.

    Both of these are well-formed values of the right shape for their scheme —
    the only thing wrong with them is the password, which is the one case that
    must contradict a server's success.
    """
    assert (
        passwordcheck.matches("mangos_srp6", "SRPPROBE", "WRONG-p@ss77", (SALT, VERIFIER)) is False
    )
    assert passwordcheck.matches("mangos_sha", "SHAPROBE", "WRONG-p@ss77", (SHA_KNOWN,)) is False
