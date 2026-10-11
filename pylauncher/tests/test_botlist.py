"""Browsing this server's bots (8.5a).

The counts already exist — 8.1a's `population()` reports how many there are.
What this adds is the list itself: paged, filtered, and saying for each row
WHICH signal identified it as a bot, because on this tree there are two and they
disagree in the cases that matter.
"""

from __future__ import annotations

import re

import pytest

from yulon import botlist, dbreads
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")
MARKER = dbreads.Marker(prefix="rndbot", source="conf")


class _Reader:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, str]] = []

    def query(self, db: str, statement: str) -> str:
        self.asked.append((db, statement))
        return self.answers.pop(0) if self.answers else ""

    def run_statement(self, db: str, statement: str) -> None:  # pragma: no cover
        raise AssertionError("browsing must not write")


def test_a_page_is_the_rows_the_server_has_with_the_signal_that_found_each() -> None:
    sql = _Reader("2\t1\t1", "Guglu\t14\t1\tregistry\t7\nRitdy\t3\t0\tprefix\t9\n")

    page = botlist.page(sql, WOTLK, MARKER)

    assert [(b.name, b.level, b.online, b.source) for b in page.bots] == [
        ("Guglu", 14, True, "registry"),
        ("Ritdy", 3, False, "prefix"),
    ]
    assert page.total == 2
    assert page.problem == ""


def test_the_split_by_signal_is_shown_because_the_two_can_disagree() -> None:
    """The registry and the name prefix are two answers to one question.

    An install whose prefix was changed after its bots were made has rows the
    registry knows about and the prefix does not; showing one number hides
    exactly that.
    """
    sql = _Reader("500\t480\t20", "")

    page = botlist.page(sql, WOTLK, MARKER)

    assert page.total == 500
    assert page.by_registry == 480
    assert page.by_prefix == 20


def test_a_marker_that_could_not_be_read_refuses_rather_than_reporting_none() -> None:
    """8.1a's rule, on a second surface.

    Zero bots and "the marker could not be read" look identical in a list, and
    on this server one of them is never true.
    """
    page = botlist.page(_Reader(), WOTLK, dbreads.Marker(prefix="", source="conf"))

    assert page.bots == []
    assert page.total is None
    assert "marker" in page.problem


def test_a_marker_matching_nothing_while_characters_exist_warns() -> None:
    sql = _Reader("0\t0\t0", "", "900")

    page = botlist.page(sql, WOTLK, MARKER)

    assert page.total == 0
    assert "900" in page.warning


def test_a_page_asks_for_one_page_and_not_the_whole_table() -> None:
    """500 bots today and the owner has said the number is his to raise."""
    sql = _Reader("500\t500\t0", "")

    botlist.page(sql, WOTLK, MARKER, limit=20)

    rows = sql.asked[1][1]
    assert "LIMIT 20" in rows


def test_the_filter_is_escaped_with_a_character_a_stricter_sql_mode_keeps() -> None:
    """A backslash is not a safe escape here.

    `NO_BACKSLASH_ESCAPES` is a real MySQL mode and a real MariaDB one, and
    under it a backslash is an ordinary character — so a filter escaped with
    one stops escaping and starts matching. The wildcards a person can type are
    `%` and `_`, and a name with an apostrophe is a name, not an injection.
    """
    sql = _Reader("1\t1\t0", "")

    botlist.page(sql, WOTLK, MARKER, name_like="10%_o'brien")

    rows = sql.asked[1][1]
    # The whole pattern, not a substring of it: asserting that "%" appears is
    # true whether or not it was escaped, which is how the first version of
    # this test survived a mutation that turned the escaping off.
    assert _like_pattern(rows) == "10!%!_o'brien%"
    assert "\\" not in rows, "a backslash escape stops escaping under NO_BACKSLASH_ESCAPES"


def _like_pattern(statement: str) -> str:
    """The LIKE pattern, read back out of its hex literal, or a failure."""
    found = re.search(r"name LIKE _utf8mb4 X'([0-9A-F]*)' ESCAPE '!'", statement)
    assert found, f"the filter is not a hex literal: {statement}"
    return bytes.fromhex(found.group(1)).decode("utf-8")


def test_a_typed_backslash_quote_cannot_end_the_filter_literal() -> None:
    """MySQL and MariaDB read a backslash inside a quoted literal by default.

    Doubling the quote is not enough then: `\\'` becomes `\\''`, which the server
    reads as an escaped quote and then a closing one, and the rest of what was
    typed is SQL. The filter goes in as hex, like every other typed value in
    the tree, so nothing a person types is parsed at all.
    """
    sql = _Reader("1\t1\t0", "")
    typed = "x\\'; DROP TABLE characters; -- "

    botlist.page(sql, WOTLK, MARKER, name_like=typed)

    for _db, statement in sql.asked:
        assert "DROP TABLE" not in statement.upper(), statement
    assert _like_pattern(sql.asked[1][1]) == typed + "%"


def test_a_database_that_cannot_be_read_says_so() -> None:
    class _Broken(_Reader):
        def query(self, db: str, statement: str) -> str:
            raise RuntimeError("the database container is not running")

    page = botlist.page(_Broken(), WOTLK, MARKER)

    assert page.total is None
    assert "not running" in page.problem


def test_a_row_that_will_not_parse_fails_the_page_rather_than_vanishing() -> None:
    sql = _Reader("2\t2\t0", "Guglu\t14\t1\tregistry\t7\nnonsense\n")

    page = botlist.page(sql, WOTLK, MARKER)

    assert page.bots == []
    assert "nonsense" in page.problem


def test_a_game_with_no_measured_marker_refuses() -> None:
    entry = load_catalog().get("wow-tortoise")
    if entry.observability is not None:  # pragma: no cover - measured since
        pytest.skip("wow-tortoise has been measured")

    page = botlist.page(_Reader(), entry, MARKER)

    assert page.total is None
    assert page.problem


def test_the_order_has_a_tiebreak_so_two_of_a_name_cannot_swap_between_pages() -> None:
    """`ORDER BY name` alone is not a total order.

    Two characters can share a name across realms on some trees, and rows with
    equal keys may come back in any order — so the same bot can appear on two
    pages and another on none. The guid breaks the tie. (Paging over a table
    that is changing underneath still shifts rows; see the module docstring.)
    """
    sql = _Reader("2\t2\t0", "")

    botlist.page(sql, WOTLK, MARKER)

    assert "ORDER BY name, guid" in sql.asked[1][1]


# -- paging that survives a list that moves (review, 2026-09-07) -------------


def test_a_page_is_asked_for_by_where_the_last_one_ended() -> None:
    """Keyset paging, not OFFSET.

    Every page is read fresh from a table that changes while the tab is open.
    With OFFSET, one bot logging out before the boundary shifts every later
    page by one: a row appears twice, and the row that took its place is never
    seen at all. A cursor is anchored to a row rather than to a count.
    """
    sql = _Reader("2\t2\t0", "")

    botlist.page(sql, WOTLK, MARKER, after=("Guglu", 42))

    rows = sql.asked[1][1]
    assert "OFFSET" not in rows
    assert "(name, guid) > (_utf8mb4 X'4775676C75', 42)" in rows  # 'Guglu'


def test_the_first_page_asks_for_no_cursor_at_all() -> None:
    sql = _Reader("2\t2\t0", "")

    botlist.page(sql, WOTLK, MARKER)

    rows = sql.asked[1][1]
    assert ">" not in rows.split("WHERE", 1)[1].split("ORDER BY")[0]


def test_a_page_says_where_the_next_one_should_start() -> None:
    """A FULL page, which is the only kind that can have anything after it."""
    sql = _Reader("2\t2\t0", "Guglu\t14\t1\tregistry\t7\nRitdy\t3\t0\tprefix\t9\n")

    page = botlist.page(sql, WOTLK, MARKER, limit=2)

    assert page.next_after == ("Ritdy", 9)


def test_the_last_page_says_there_is_no_next_one() -> None:
    """Asked for fifty and given two: there is nothing after them."""
    sql = _Reader("2\t2\t0", "Guglu\t14\t1\tregistry\t7\nRitdy\t3\t0\tprefix\t9\n")

    page = botlist.page(sql, WOTLK, MARKER, limit=50)

    assert page.next_after is None


def test_a_full_page_offers_a_next_one() -> None:
    rows = "".join(f"Bot{n}\t1\t0\tregistry\t{n}\n" for n in range(2))
    sql = _Reader("9\t9\t0", rows)

    page = botlist.page(sql, WOTLK, MARKER, limit=2)

    assert page.next_after == ("Bot1", 1)


# -- 8.5b: the trees that have only one signal --------------------------------


def test_a_tree_with_no_registry_has_no_second_signal_to_split_by() -> None:
    """8.5b. TBC, Vanilla and Tortoise have one signal; WotLK has two.

    The split is worth showing only where the two can disagree. On a tree whose
    catalog entry carries no registry there is nothing to disagree with, and
    naming one is naming a table the install has not got — measured on m910q's
    TBC install on 2026-09-07, whose schema list is exactly `characters, logs,
    realmd, mangos, sys, mysql, performance_schema`.
    """
    catalog = load_catalog()

    assert botlist.has_registry(catalog.get("wow-wotlk")) is True
    assert botlist.has_registry(catalog.get("wow-tbc")) is False
    assert botlist.has_registry(catalog.get("wow-vanilla")) is False
    assert botlist.has_registry(catalog.get("wow-tortoise")) is False


def test_the_split_flag_and_the_attribution_clause_cannot_disagree() -> None:
    """One reading of the catalog, not two.

    `_registry_clause` degenerating to `1 = 0` and "this tree has no second
    signal" are the same fact. Asserted over every game rather than over the
    one this box gates, because the failure this guards against is a later
    hand-written flag that is right on TBC and wrong on the next tree.
    """
    for entry in load_catalog().games:
        ops = entry.observability
        if ops is None:
            continue
        degenerate = botlist._registry_clause(entry, ops) == "1 = 0"
        assert degenerate is not botlist.has_registry(entry), entry.id


# -- 8.5c: the two ways a zero happens, told apart -----------------------------


VANILLA = load_catalog().get("wow-vanilla")
"""8.5c's tree. One signal, the account prefix, like TBC and Tortoise."""


def test_a_filter_that_matches_nothing_does_not_blame_the_marker() -> None:
    """Two causes of a zero, and only one of them is the marker's fault.

    The name filter is folded into the same WHERE the total is counted over, so
    a filter matching nothing drives `total == 0` down the same branch as a
    marker matching nothing — and the tab then said *"no character matched the
    bot marker 'RNDBOT'"* about a marker that had matched 500 rows a keystroke
    earlier. On a one-signal tree that sentence is the whole proof of 8.5b's
    third clause, so a second way to produce it makes the gate's evidence unable
    to identify its own cause (adversarial review of 8.5c's plan, 2026-09-07).
    """
    sql = _Reader("0\t0\t0", "", "500")

    page = botlist.page(sql, VANILLA, MARKER, name_like="Zzz")

    assert page.total == 0
    assert "marker" not in page.warning, page.warning
    assert "'Zzz'" in page.warning and "500" in page.warning, page.warning


def test_the_zero_a_filter_made_is_measured_against_the_bots_not_the_characters() -> None:
    """The number the two sentences quote is not the same number.

    An unfiltered zero is compared with the whole characters table, because the
    question it answers is "did this marker find anybody at all on a populated
    server". A filtered zero is compared with the bots the marker DID find,
    because the question is "how many of those does this filter exclude" — and
    quoting 900 characters there would invite exactly the wrong reading.

    Asserted through the statements the reader was handed rather than by reading
    the source, which is how `_Reader.asked` is used throughout this file.
    """
    filtered = _Reader("0\t0\t0", "", "500")
    botlist.page(filtered, VANILLA, MARKER, name_like="Zzz")

    unfiltered = _Reader("0\t0\t0", "", "900")
    botlist.page(unfiltered, VANILLA, MARKER)

    third_filtered = filtered.asked[2][1]
    third_unfiltered = unfiltered.asked[2][1]
    assert "LIKE 'RNDBOT%'" in third_filtered, third_filtered
    assert "name LIKE" not in third_filtered, "the filter must not narrow its own denominator"
    assert "LIKE" not in third_unfiltered, third_unfiltered


def test_a_filter_that_matches_something_says_nothing_at_all() -> None:
    """The sentence exists to explain a zero, and there is no zero here."""
    sql = _Reader("3\t0\t3", "Arm\t9\t1\tprefix\t4\n")

    page = botlist.page(sql, VANILLA, MARKER, name_like="Arm")

    assert page.total == 3
    assert page.warning == ""
    assert len(sql.asked) == 2, "a page that found rows must not pay for a third query"


def test_a_filter_on_a_server_the_marker_found_nothing_on_still_blames_the_marker() -> None:
    """Both zeros at once, and the marker is the one worth saying.

    A filter typed on a server where the marker matched nothing would otherwise
    answer "no bot's name begins with 'Zzz'" — true, and it hides the fact that
    there are no bots here under either question. `_why_zero` asks the bots
    first, gets a zero, and falls through to the marker's sentence.

    Asserted because the docstring claims it: a documented fallthrough that
    nothing checks is a declaration, not a behaviour.
    """
    sql = _Reader("0\t0\t0", "", "0", "900")

    page = botlist.page(sql, VANILLA, MARKER, name_like="Zzz")

    assert "no character matched the bot marker" in page.warning, page.warning
    assert "900" in page.warning
