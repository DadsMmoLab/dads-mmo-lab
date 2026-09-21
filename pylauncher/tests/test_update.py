"""Tests for the self-update check (`yulon.update`, README §10): what one answer means."""

from __future__ import annotations

import email.message
import importlib.util
import json
import random
import time
import urllib.error
from fractions import Fraction
from pathlib import Path

import pytest

from tests.support_update import FEED, release
from yulon.update import (
    MAX_BODY_CHARS,
    MAX_NOTES_CHARS,
    RELEASES_API,
    RELEASES_PAGE,
    RELEASES_REPO,
    check_for_update,
    clipped_body,
    evaluate_feed,
    is_newer,
    is_public_tag,
    public_tag,
    safe_release_url,
    version_key,
)

THE_REAL_TAG_HISTORY = [
    "v0.6.5",
    "v0.6.51",
    "v0.6.52",
    "v0.6.53",
    "v0.6.55",
    "v0.6.57",
    "v0.6.58",
    "v0.6.59",
    "v0.8.0",
    "v0.8.4",
    "v0.8.5",
    "v0.8.6",
    "v0.8.65",
    "v0.8.7",
]
"""Every version this project has tagged, oldest first, as the owner listed it.

The fixture is the real history and not a made-up one because the ordering rule
is a fact about THIS project's tags: `v0.8.7-Public` was cut on 2026-09-19,
after `v0.8.65-Public` on 2026-09-13, and the fork's `-fixtest` tags .66 .67
.68 .69 sit between them.
"""


def fake_github(url: str) -> str:
    """Both release endpoints, answering the way GitHub answers them for this repo."""
    if url.endswith("/releases/latest"):
        # `/releases/latest` means "latest non-prerelease". There isn't one, so
        # GitHub has 404'd this call for every user since the feature shipped.
        raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)
    if "/releases" in url:
        return FEED
    raise AssertionError(f"the check asked for an endpoint nobody serves: {url}")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v1.2.3", (1, 2, Fraction(3, 10))),
        ("1.2.3", (1, 2, Fraction(3, 10))),
        ("v0.1.4-rc1", (0, 1, Fraction(4, 10))),
        ("v0.8.65", (0, 8, Fraction(65, 100))),
        ("v0.8.7", (0, 8, Fraction(7, 10))),
        ("v0.8.70", (0, 8, Fraction(7, 10))),
        ("v0.8.0", (0, 8, Fraction(0, 1))),
        ("nightly", None),
        ("", None),
    ],
)
def test_version_key_reads_the_last_number_as_a_decimal(
    text: str, expected: tuple[int, int, Fraction] | None
) -> None:
    """`65` is .65 and `7` is .7, so `.7` is the newer of the two."""
    assert version_key(text) == expected


def test_the_key_is_exact_arithmetic_and_not_a_float() -> None:
    """Floats would make `.1 + .2` style comparisons answer by luck rather than by rule."""
    key = version_key("v0.8.65")
    assert key is not None
    assert isinstance(key[2], Fraction)


def test_the_real_tag_history_sorts_into_the_order_it_was_cut_in() -> None:
    """The whole point, against every tag this project has actually published."""
    keys = [version_key(tag) for tag in THE_REAL_TAG_HISTORY]
    assert None not in keys

    assert sorted(THE_REAL_TAG_HISTORY, key=lambda tag: version_key(tag) or ()) == (
        THE_REAL_TAG_HISTORY
    )


def test_the_fork_test_tags_sit_between_the_two_public_releases_they_were_cut_between() -> None:
    """.66 .67 .68 .69 are after 0.8.65 and before 0.8.7, which is what the dates say."""
    between = [version_key(f"v0.8.{n}") for n in (66, 67, 68, 69)]
    low, high = version_key("v0.8.65"), version_key("v0.8.7")

    assert low is not None and high is not None
    assert all(key is not None and low < key < high for key in between)


def test_is_newer_compares_numerically_not_lexically() -> None:
    assert is_newer("v0.10.0", "0.9.9") is True
    assert is_newer("v0.1.4", "0.1.4") is False
    assert is_newer("garbage", "0.1.4") is False


def test_a_shorter_last_number_can_still_be_newer() -> None:
    """The defect: as integers 65 > 7, so 0.8.7 was never offered to anybody on 0.8.65."""
    assert is_newer("v0.8.7", "0.8.65") is True
    assert is_newer("v0.8.65", "0.8.7") is False


def test_trailing_zeroes_do_not_make_a_version_newer() -> None:
    """`.7` and `.70` are the same number, so neither is an update to the other."""
    assert is_newer("v0.8.70", "0.8.7") is False
    assert is_newer("v0.8.7", "0.8.70") is False
    assert version_key("v0.8.70") == version_key("v0.8.7")


def test_the_minor_number_is_still_a_whole_number() -> None:
    """Only the LAST number is a fraction; 0.9.0 outranks every 0.8.x there can be."""
    assert is_newer("v0.9.0", "0.8.99") is True
    assert is_newer("v0.8.99", "0.9.0") is False


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        # A fourth number is not this scheme, and reading it as `.7` was silent.
        ("v0.8.7.1", None),
        ("v1.2.3.4-Public", None),
        ("v0.8.7.", None),
        # `PUBLIC_TAG` is case-insensitive, so a `V` tag passes the filter. It
        # has to key too, or the release is dropped without a word.
        ("V0.8.7-Public", (0, 8, Fraction(7, 10))),
        ("V1.2.3", (1, 2, Fraction(3, 10))),
        # A suffix is fine here: deciding what is a PUBLIC tag is not this
        # function's job, it is `is_public_tag`'s.
        ("v0.8.7-Public-rc1", (0, 8, Fraction(7, 10))),
        ("v1.2.3-Public-rc1", (1, 2, Fraction(3, 10))),
        ("v0.6.59Public", (0, 6, Fraction(59, 100))),
        ("0.8.70-Public", (0, 8, Fraction(7, 10))),
        # Nine digits is the cap, and the tenth is refused rather than trimmed.
        ("v0.8.999999999", (0, 8, Fraction(999999999, 10**9))),
        ("v0.8.9999999999", None),
        ("v999999999.2.3", (999999999, 2, Fraction(3, 10))),
        ("v9999999999.2.3", None),
        ("v0.9999999999.3", None),
    ],
)
def test_version_key_refuses_what_it_cannot_read_rather_than_guessing(
    tag: str, expected: tuple[int, int, Fraction] | None
) -> None:
    assert version_key(tag) == expected


def test_a_capital_v_release_is_not_dropped_in_silence() -> None:
    """`is_public_tag` accepts it, so the key has to as well or it vanishes.

    The two filters are ANDed in `_public_releases`: a tag that passes one and
    keys to None is skipped with nothing said about it.
    """
    assert is_public_tag("V0.8.7-Public") is True
    assert version_key("V0.8.7-Public") is not None

    feed = json.dumps([release("V0.8.7-Public", body="### New\n- Seven.")])
    assert evaluate_feed(feed, "0.8.65-Public").latest == "V0.8.7-Public"


def test_a_number_too_long_to_be_a_version_does_not_raise_out_of_the_check() -> None:
    """CPython refuses `int()` on a string of more than 4300 digits (a ValueError).

    Measured before the cap: a tag of `v0.8.` + 5000 nines raised that straight
    out of `evaluate_feed`, whose contract is "ValueError on non-JSON only" —
    and out of `_from_cache`, where it reads as a corrupt cache and re-fetches
    every launch for as long as the feed holds the tag.
    """
    monstrous = "v0.8." + "9" * 5000

    assert version_key(monstrous) is None

    feed = json.dumps([release(f"{monstrous}-Public"), release("v0.8.7-Public")])
    result = evaluate_feed(feed, "0.8.65-Public")

    assert result.latest == "v0.8.7-Public", "the sane release is still found"


@pytest.mark.parametrize(
    ("lower", "higher"),
    [("v0.8.05", "v0.8.1"), ("v0.8.05", "v0.8.06"), ("v0.8.0", "v0.8.05")],
)
def test_a_leading_zero_is_a_smaller_fraction_and_that_is_intended(lower: str, higher: str) -> None:
    """`.05` is five hundredths, so it sits below `.1` — the digits are the number."""
    a, b = version_key(lower), version_key(higher)

    assert a is not None and b is not None and a < b
    assert is_newer(higher, lower) is True
    assert is_newer(lower, higher) is False


def test_the_known_cost_of_the_scheme_is_recorded_rather_than_worked_around() -> None:
    """`0.8.10` orders BEFORE `0.8.9`, because .10 is a tenth and .9 is nine tenths.

    Stated, not fixed (owner, 2026-09-21): the scheme has never produced a tag
    like that, and every rule that would special-case it also changes the
    meaning of the tags that do exist. A test so the next reader finds the
    decision instead of the surprise.
    """
    assert is_newer("v0.8.10", "0.8.9") is False
    assert is_newer("v0.8.9", "0.8.10") is True


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.8.70-Public", True),
        ("v0.8.70-public", True),
        ("v0.8.70-PUBLIC", True),
        ("v0.8.71-fixtest", False),
        ("v0.8.5-DeckTest", False),
        ("v0.6.60-phase8", False),
        ("v0.6.59Public", False),
        ("v0.6.59", False),
        ("0.8.70-Public", False),
        ("", False),
        ("v0.8.70-Public-rc1", False),
    ],
)
def test_only_a_dash_public_tag_is_a_release_for_players(tag: str, expected: bool) -> None:
    """The defect this closes: a test tag shares this feed and was offered to players.

    `-fixtest` and `-DeckTest` builds are cut from the same repository, and the
    check compared the numeric triple only — so the newest thing anybody had
    tagged became the update every user was told to install.
    """
    assert is_public_tag(tag) is expected


def test_a_test_tag_is_never_offered() -> None:
    result = evaluate_feed(FEED, "0.8.66-Public")
    assert result.latest == "v0.8.70-Public" and result.available


def test_the_newest_is_by_version_not_by_position_and_a_draft_does_not_count() -> None:
    """The feed is ordered by creation date, and a draft sits in it invisibly."""
    assert evaluate_feed(FEED, "0.8.0-Public").latest == "v0.8.70-Public"


def test_notes_cover_every_public_release_newer_than_mine_newest_first() -> None:
    """One entry per release, in order, each holding that release's body and no more."""
    notes = evaluate_feed(FEED, "0.8.66-Public").notes

    assert [(n.tag, n.body, n.cut) for n in notes] == [
        ("v0.8.70-Public", "### New\n- Ten.", False),
        ("v0.8.69-Public", "### Fixed\n- Nine.", False),
    ]


def test_notes_stop_at_my_version() -> None:
    assert "Six." not in "".join(n.body for n in evaluate_feed(FEED, "0.8.66-Public").notes)


def test_assets_and_the_checksum_file_are_carried() -> None:
    result = evaluate_feed(FEED, "0.8.66-Public")
    assert [a.name for a in result.assets] == ["Yulon-v0.8.70-Public-x86_64.AppImage", "SHA256SUMS"]
    assert result.assets[0].size == 10
    assert result.assets[0].url.endswith(".AppImage")
    assert result.has_checksums


def test_up_to_date_is_not_an_error() -> None:
    result = evaluate_feed(FEED, "0.8.70-Public")
    assert not result.available and result.error is None and result.latest == "v0.8.70-Public"
    assert result.notes == () and not result.notes_cut


def test_a_test_build_newer_than_every_public_release_is_offered_nothing() -> None:
    """A developer running `0.8.71-fixtest` is not told to downgrade to 0.8.70."""
    assert not evaluate_feed(FEED, "0.8.71-fixtest").available


# ----------------------------------------- the real feed, ordered the real way

REAL_ORDER_FEED = json.dumps(
    [
        release("v0.8.7-Public", body="### New\n- Seven."),
        release("v0.8.65-Public", body="### New\n- Sixty-five."),
    ]
)
"""The two releases the ordering defect was measured on, newest BY DATE first."""


def test_the_release_cut_after_the_longer_numbered_one_is_the_one_offered() -> None:
    """Upstream cut v0.8.7-Public (2026-09-19) after v0.8.65-Public (2026-09-13).

    Compared as integers, 65 > 7, so nobody on 0.8.65 or 0.8.66 was ever
    offered it — and picking the newest BY VERSION made that worse than the old
    first-in-feed rule, which would at least have found the top entry.
    """
    result = evaluate_feed(REAL_ORDER_FEED, "0.8.66-Public")

    assert result.latest == "v0.8.7-Public"
    assert result.available


def test_the_version_that_was_offered_is_not_offered_again() -> None:
    assert not evaluate_feed(REAL_ORDER_FEED, "0.8.7-Public").available


def test_the_notes_hold_only_what_is_newer_under_the_decimal_rule() -> None:
    """From 0.8.65 the only newer public release is 0.8.7, and .65 itself is not in it."""
    notes = evaluate_feed(REAL_ORDER_FEED, "0.8.65-Public").notes

    assert [n.tag for n in notes] == ["v0.8.7-Public"]
    assert "Seven." in notes[0].body
    assert "Sixty-five." not in notes[0].body


# ------------------------------------------- what a body may cost the GUI thread


def test_one_release_body_is_cut_at_its_cap() -> None:
    """Measured: 125 KB of `` `a `` took 10.2 s to lay out, on the GUI thread."""
    huge = "\n".join("a line of notes" for _ in range(20000))
    feed = json.dumps([release("v0.9.0-Public", body=huge)])

    notes = evaluate_feed(feed, "0.8.0-Public").notes

    assert (
        len(notes[0].body) <= MAX_BODY_CHARS
    ), f"a body of {len(notes[0].body)} reached the dialog"
    assert notes[0].cut, "the reader is not told the body was cut"
    assert notes[0].body.startswith("a line of notes"), "the beginning is still shown"


def test_the_notes_of_many_releases_are_capped_together() -> None:
    """Twenty releases each under the per-body cap can still add up to minutes.

    Whole releases are dropped from the OLD end, never cut in the middle: the
    budget is over releases, so nothing in `update.py` needs an opinion about
    markdown.
    """
    body = "\n".join("x" * 60 for _ in range(200))
    feed = json.dumps([release(f"v0.9.{n}-Public", body=body) for n in range(9, 0, -1)])

    result = evaluate_feed(feed, "0.8.0-Public")

    assert sum(len(n.body) for n in result.notes) <= MAX_NOTES_CHARS
    assert all(not n.cut for n in result.notes), "a release was cut to make the budget"
    assert result.notes[0].tag == "v0.9.9-Public", "the newest release was dropped"


def test_a_cut_falls_on_a_line_boundary() -> None:
    text = "\n".join(f"line {n}" for n in range(1000))

    cut, was_cut = clipped_body(text, 100)

    assert was_cut
    assert all(line.startswith("line ") for line in cut.split("\n"))
    assert len(cut) <= 100


def test_one_enormous_line_is_cut_anyway() -> None:
    """There is no boundary to prefer, and the bound is the point."""
    cut, was_cut = clipped_body("`a" * 62500, 1000)

    assert was_cut and len(cut) == 1000


def test_a_line_that_fits_is_never_cut_into() -> None:
    """`Intro` then 100,000 characters keeps `Intro` whole and cuts only the giant line."""
    cut, _ = clipped_body("Intro\n" + "x" * 100000, MAX_BODY_CHARS)

    assert cut == "Intro", "a line that could not fit took the one that could with it"


def test_notes_that_fit_are_left_exactly_alone() -> None:
    """Byte for byte, including the line endings: an uncut body is not rewritten."""
    ordinary = "### Fixed\r\n- One thing.\r\n\r\n```\r\nunclosed\r\n"

    assert clipped_body(ordinary, MAX_BODY_CHARS) == (ordinary, False)
    assert not any(n.cut for n in evaluate_feed(FEED, "0.8.66-Public").notes)


def test_capping_a_capped_body_changes_nothing() -> None:
    once, _ = clipped_body("\n".join(f"line {n}" for n in range(5000)), 4096)

    assert clipped_body(once, 4096) == (once, False)


def _blocks(document: object) -> list:
    out = []
    block = document.begin()  # type: ignore[attr-defined]
    while block.isValid():
        out.append(block)
        block = block.next()
    return out


# --------------------------------- one release's markdown may not reach another

HOSTILE_BODIES = {
    # No cap involved at all: this body is 19 characters. Glued into one
    # markdown string it put EVERY later release's heading inside a code block
    # (sixth cold review, 2026-09-21).
    "unclosed fence, uncut": "```\nshort unclosed",
    "unclosed fence in a list": "- x\n\n  ```\n  short",
    # md4c closes a fence when the list holding it ends; a line-by-line fence
    # tracker cannot see a container, so it said "still open" and appended a
    # closer that OPENED one.
    "list fence, then the list ends": "- a\n\n  ```\n  code\n\n"
    + "\n\n".join(f"plain paragraph {n}" for n in range(2000)),
    "ordered item, three-space fence": "1. step\n   ```\n   code\n\n"
    + "\n\n".join(f"plain paragraph {n}" for n in range(2000)),
    "block quote fence": "> ```\n> code\n\n"
    + "\n\n".join(f"plain paragraph {n}" for n in range(2000)),
    # `rstrip()` is Unicode-wide and md4c is not: a no-break space after a
    # closer means the line is not a closer to md4c and is to Python.
    "no-break space after the closer": "```\ncode\n``` \n```\n"
    + "\n".join(f"code line {n}" for n in range(3000)),
    "form feed after the closer": "```\ncode\n```\x0c\n```\n"
    + "\n".join(f"code line {n}" for n in range(3000)),
    # `startswith("#")` is not what a heading is: these are paragraphs, and
    # calling them headings meant the cut dropped every one of them.
    "issue references": "\n".join(f"#{n} fixed a thing in the launcher" for n in range(2000)),
    "shell comments inside a fence": "```sh\n" + "\n".join(f"# comment {n}" for n in range(3000)),
    "fence at the top, cut inside": "```\n" + "\n".join(f"code line {n}" for n in range(3000)),
    "tilde fence holding backticks": "~~~\n```\n"
    + "\n".join(f"code line {n}" for n in range(3000)),
    "long opener, short closer": "`````\n```\n" + "\n".join(f"code line {n}" for n in range(3000)),
    "a table": "\n".join("| a | b |" for _ in range(3000)),
    "nothing at all": "",
}


def _rendered(result: object, qapp: object) -> tuple[list, object]:
    """`result`'s notes through the real widget. Returns (blocks, the view)."""
    from yulon.ui.widgets.update_dialog import _NotesView

    view = _NotesView()
    view.set_release_notes(result.notes, notes_cut=result.notes_cut)  # type: ignore[attr-defined]
    return _blocks(view.document()), view


def _is_code(block: object) -> bool:
    """Qt marks a code block on the block format; a monospaced run shows on the char one."""
    return bool(
        block.blockFormat().hasProperty(0x1090)  # type: ignore[attr-defined]
        or block.charFormat().fontFixedPitch()  # type: ignore[attr-defined]
    )


@pytest.mark.parametrize("name", list(HOSTILE_BODIES))
def test_one_releases_markdown_never_reaches_the_next(name: str, qapp: object) -> None:
    """Every release's tag is a real heading, whatever the release before it did.

    Asserted by RENDERING, because "is this a heading" is a question about the
    document rather than about a string — and every defect this replaces was
    invisible in the string.
    """
    feed = json.dumps(
        [
            release("v1.3.0-Public", body=HOSTILE_BODIES[name]),
            release("v1.2.0-Public", body="### Fixed\n- older."),
            release("v1.1.0-Public", body="### Fixed\n- oldest."),
        ]
    )

    blocks, view = _rendered(evaluate_feed(feed, "1.0.0-Public"), qapp)
    try:
        tags = {
            block.text().strip(): (block.blockFormat().headingLevel(), _is_code(block))
            for block in blocks
            if "-Public" in block.text()
        }

        assert tags == {
            "v1.3.0-Public": (2, False),
            "v1.2.0-Public": (2, False),
            "v1.1.0-Public": (2, False),
        }, f"{name}: a tag is not a heading of its own"
    finally:
        view.deleteLater()  # type: ignore[attr-defined]


@pytest.mark.parametrize("position", [0, 1, 2])
def test_a_hostile_body_is_harmless_wherever_it_sits(position: int, qapp: object) -> None:
    """First, middle or last section: the isolation is not an artefact of order."""
    bodies = ["### Fixed\n- one.", "### Fixed\n- two.", "### Fixed\n- three."]
    bodies[position] = "```\nshort unclosed"
    feed = json.dumps([release(f"v1.{3 - n}.0-Public", body=body) for n, body in enumerate(bodies)])

    blocks, view = _rendered(evaluate_feed(feed, "1.0.0-Public"), qapp)
    try:
        levels = [
            block.blockFormat().headingLevel() for block in blocks if "-Public" in block.text()
        ]

        assert levels == [2, 2, 2], f"a body in slot {position} reached the others"
    finally:
        view.deleteLater()  # type: ignore[attr-defined]


def _random_body(rnd: random.Random) -> str:
    """A body from the shapes that broke every textual version of this cap."""
    pieces = []
    for _ in range(rnd.randint(1, 30)):
        kind = rnd.random()
        if kind < 0.18:
            opener = rnd.choice(["```", "~~~", "````", "```sh", "```a`b", "   ```", "\t```"])
            closer = rnd.choice(["```", "~~~", "``` ", "``` ", "```\x0c", ""])
            pieces.append(
                opener + "\n" + "\n".join(f"# code {n}" for n in range(3)) + "\n" + closer
            )
        elif kind < 0.3:
            pieces.append("- item\n\n  ```\n  " + rnd.choice(["code", "code\n  ```"]))
        elif kind < 0.38:
            pieces.append("> ```\n> quoted code")
        elif kind < 0.46:
            pieces.append(f"#{rnd.randint(1, 999)} fixed a thing")
        elif kind < 0.54:
            pieces.append("#" * rnd.randint(1, 4) + f" heading {rnd.randint(0, 9)}")
        elif kind < 0.6:
            pieces.append("| a | b |\n| - | - |\n| 1 | 2 |")
        elif kind < 0.68:
            pieces.append("x" * rnd.randint(10, 4000))
        else:
            pieces.append("word " * rnd.randint(1, 40))
    return "\n\n".join(pieces)


def test_random_hostile_feeds_always_render_one_heading_per_release(qapp: object) -> None:
    """A fixed INTEGER seed, so a failure reproduces on both CI legs.

    `random.Random(hash("some string"))` is not seeded at all: `PYTHONHASHSEED`
    is random per process, so the round-5 tests that used it ran a different
    case on every run and on every leg (sixth cold review, 2026-09-21).
    """
    from yulon.ui.widgets.update_dialog import _NotesView

    rnd = random.Random(1234)
    view = _NotesView()
    try:
        for trial in range(40):
            count = rnd.randint(1, 5)
            feed = json.dumps(
                [release(f"v1.{n}.0-Public", body=_random_body(rnd)) for n in range(count, 0, -1)]
            )
            result = evaluate_feed(feed, "1.0.0-Public")
            view.set_release_notes(result.notes, notes_cut=result.notes_cut)

            # A body may hold its own `## heading`, so the tags are picked out
            # by name rather than by being the first level-2 blocks.
            wanted = [n.tag for n in result.notes]
            seen = [
                (block.text().strip(), block.blockFormat().headingLevel(), _is_code(block))
                for block in _blocks(view.document())
                if block.text().strip() in set(wanted)
            ]

            assert [text for text, _, _ in seen] == wanted, (
                f"trial {trial}: the tags are {[t for t, _, _ in seen][:4]}, not the releases "
                "in order — one body reached another, or a tag was rendered twice"
            )
            assert all(
                level == 2 and not code for _, level, code in seen
            ), f"trial {trial}: a tag is not a plain level-2 heading: {seen}"
    finally:
        view.deleteLater()


# ------------------------------------------------- the cap still keeps content


def test_the_newest_release_is_never_the_one_that_is_dropped() -> None:
    """Whatever else is left out, the release the banner is about has to be there."""
    for width in (63, 64, 65, 100):
        body = "### Fixed\n" + "\n".join("- " + "w" * width for _ in range(12))
        feed = json.dumps([release(f"v1.{n}.0-Public", body=body) for n in range(400, 0, -1)])

        result = evaluate_feed(feed, "1.0.0-Public")

        assert result.notes[0].tag == "v1.400.0-Public", f"width {width}"
        assert result.notes[0].body == body, f"the newest release was cut (width {width})"
        assert result.notes_cut, "older releases were dropped and nothing says so"


def test_seven_hundred_releases_still_show_the_newest() -> None:
    """The count that first overflowed the cap, now asked what it kept."""
    feed = json.dumps([release(f"v1.{n}.0-Public", body="x" * 100) for n in range(700, 0, -1)])

    result = evaluate_feed(feed, "1.0.0-Public")

    assert result.notes[0].tag == "v1.700.0-Public"
    assert result.notes[0].body == "x" * 100
    assert sum(len(n.body) for n in result.notes) <= MAX_NOTES_CHARS


def test_a_body_of_lines_one_width_keeps_what_fits() -> None:
    """The round-4 cap answered this one with the trailer and nothing else."""
    body = "\n".join(["a" * 68] * 2000)

    kept, was_cut = clipped_body(body, MAX_BODY_CHARS)

    assert was_cut
    assert len(kept) > MAX_BODY_CHARS - 70, f"only {len(kept)} of {MAX_BODY_CHARS} used"
    assert kept.startswith("a" * 68)


@pytest.mark.parametrize("width", range(60, 130))
def test_no_line_width_loses_the_body(width: int) -> None:
    """68 was found by sweeping; the sweep is the test, so the next one cannot hide."""
    kept, _ = clipped_body("\n".join(["a" * width] * 2000), MAX_BODY_CHARS)

    assert len(kept) > MAX_BODY_CHARS - width - 2, f"only {len(kept)} used at width {width}"


def test_random_bodies_keep_what_fits() -> None:
    """Seeded with an integer, so a failure reproduces."""
    rnd = random.Random(20260921)
    for _ in range(400):
        lines = ["- " + "w" * rnd.randint(20, 110) for _ in range(1500)]
        body = "\n".join(lines)
        limit = rnd.choice([MAX_BODY_CHARS, MAX_NOTES_CHARS, 200, 5000])

        kept, was_cut = clipped_body(body, limit)

        assert len(kept) <= limit
        assert was_cut == (len(body) > limit)
        longest = max(len(line) for line in lines)
        assert len(kept) > limit - longest - 1, f"only {len(kept)} of {limit} used"


def test_a_feed_with_no_public_release_says_so() -> None:
    result = evaluate_feed(json.dumps([release("v0.8.71-fixtest")]), "0.8.0")
    assert not result.available and result.error == "no public release"


def test_a_rate_limit_body_is_json_but_not_a_feed() -> None:
    """`{"message": "API rate limit exceeded"}` is valid JSON and nothing to offer."""
    result = evaluate_feed(json.dumps({"message": "API rate limit exceeded"}), "0.8.0")
    assert not result.available and result.error == "no public release"


def test_an_empty_feed_is_no_public_release() -> None:
    assert evaluate_feed("[]", "0.8.0").error == "no public release"


def test_a_body_that_is_not_json_at_all_raises_for_the_caller_to_catch() -> None:
    """`evaluate_feed` is pure and says so by raising; `check_for_update` never does."""
    with pytest.raises(ValueError):
        evaluate_feed("<html>rate limited</html>", "0.8.0")


def test_malformed_assets_are_dropped_not_fatal() -> None:
    feed = json.dumps(
        [
            {
                **release("v0.9.0-Public"),
                "assets": [
                    {"name": 3},
                    "junk",
                    {"name": "a", "browser_download_url": "u", "size": "big"},
                ],
            }
        ]
    )
    assert evaluate_feed(feed, "0.8.0").assets == ()


def test_the_fallback_page_is_one_that_exists() -> None:
    """`/releases/latest` 404s here: every release this project cuts is a prerelease."""
    assert RELEASES_PAGE.endswith("/releases")


def test_the_feed_asks_for_as_many_releases_as_the_api_will_give() -> None:
    """20 meant a week of test tags could push the newest public release off the page."""
    assert "per_page=100" in RELEASES_API


def test_both_urls_name_the_same_repository() -> None:
    """A gate that repoints the feed must not leave the opener trusting the other repo."""
    assert RELEASES_REPO in RELEASES_API and RELEASES_REPO in RELEASES_PAGE


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v0.8.70-Public", True),
        ("https://github.com/DadsMmoLab/dads-mmo-lab", True),
        ("https://github.com/DadsMmoLab/dads-mmo-lab/releases", True),
        ("http://github.com/DadsMmoLab/dads-mmo-lab/releases", False),
        ("https://github.com.evil.example/DadsMmoLab/dads-mmo-lab/releases", False),
        ("https://github.com/DadsMmoLabX/dads-mmo-lab/releases", False),
        ("https://github.com/DadsMmoLab/dads-mmo-lab-evil/releases", False),
        ("file:///etc/passwd", False),
        ("//host/share/x.exe", False),
        ("", False),
    ],
)
def test_only_this_repositorys_own_pages_are_ever_opened(url: str, allowed: bool) -> None:
    """`html_url` is a string the feed chose; `QDesktopServices` starts what it says."""
    assert safe_release_url(url) == (url if allowed else RELEASES_PAGE)


@pytest.mark.parametrize(
    "url",
    [
        # A browser normalises this to github.com/other — the prefix check alone
        # never saw it leave (second cold review, 2026-09-21).
        "https://github.com/DadsMmoLab/dads-mmo-lab/../../other",
        "https://github.com/DadsMmoLab/dads-mmo-lab/./x",
        # A TRAILING `..` — the prefix matched and `/../` never saw it.
        "https://github.com/DadsMmoLab/dads-mmo-lab/..",
        "https://github.com/DadsMmoLab/dads-mmo-lab/..?x",
        "https://github.com/DadsMmoLab/dads-mmo-lab/..#x",
        "https://github.com/DadsMmoLab/dads-mmo-lab/.",
        "https://github.com/DadsMmoLab/dads-mmo-lab/x/..",
        "https://github.com/DadsMmoLab/dads-mmo-lab/%2e%2e/%2e%2e/other",
        "https://github.com/DadsMmoLab/dads-mmo-lab/%2E%2E/other",
        "https://github.com/DadsMmoLab/dads-mmo-lab\\..\\other",
        "https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v1 2",
        "https://github.com/DadsMmoLab/dads-mmo-lab/releases\nSet-Cookie: x",
        "https://github.com/DadsMmoLab/dads-mmo-lab/releases\tx",
        "https://user@github.com/DadsMmoLab/dads-mmo-lab/releases",
        "https://user:pw@github.com/DadsMmoLab/dads-mmo-lab/releases",
        "https://github.com:8443/DadsMmoLab/dads-mmo-lab/releases",
        "https://GitHub.com.evil.example/DadsMmoLab/dads-mmo-lab/releases",
    ],
)
def test_a_url_that_could_normalise_somewhere_else_is_refused(url: str) -> None:
    """Prefix-matching a URL is not the same as knowing where it goes."""
    assert safe_release_url(url) == RELEASES_PAGE


def test_the_ordinary_release_url_still_passes_the_stricter_rule() -> None:
    good = "https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v0.8.7-Public"

    assert safe_release_url(good) == good


def test_the_allowed_prefix_follows_the_configured_repository() -> None:
    """A fork gate points the feed at pjerra; the opener has to move with it."""
    fork = "https://github.com/pjerra/dads-mmo-lab/releases"

    assert safe_release_url(f"{fork}/tag/v0.0.1-Public", page=fork).startswith(fork)
    assert safe_release_url(RELEASES_PAGE, page=fork) == fork


def test_a_feed_of_nothing_but_prereleases_still_yields_an_update() -> None:
    """The endpoint that lists them all, not `/releases/latest`, which 404s for everyone."""
    result = check_for_update("0.8.66-Public", http_get=fake_github)

    assert result.available is True
    assert result.latest == "v0.8.70-Public"
    assert result.url.endswith("/releases/tag/v0.8.70-Public")
    assert result.error is None


def test_check_reports_available_only_for_a_newer_release() -> None:
    payload = json.dumps([release("v9.9.9-Public")])
    result = check_for_update("0.1.4", http_get=lambda url: payload)
    assert result.available is True and result.latest == "v9.9.9-Public"
    assert result.url.endswith("/releases/tag/v9.9.9-Public") and result.error is None

    same = check_for_update("9.9.9", http_get=lambda url: payload)
    assert same.available is False and same.latest == "v9.9.9-Public"


def test_check_degrades_cleanly_offline_or_on_odd_payloads() -> None:
    def offline(url: str) -> str:
        raise OSError("no network")

    off = check_for_update("0.1.4", http_get=offline)
    assert off.available is False and off.latest is None and off.url == RELEASES_PAGE
    assert off.error is not None and "no network" in off.error

    broken = check_for_update("0.1.4", http_get=lambda url: "<html>rate limited</html>")
    assert broken.available is False and broken.error is not None

    # A rate-limited answer is valid JSON, just not a feed: `{"message": "API rate
    # limit exceeded", ...}`. Nothing published in it, and nothing to crash on.
    throttled = check_for_update("0.1.4", http_get=lambda url: json.dumps({"message": "slow down"}))
    assert throttled.available is False and throttled.latest is None
    assert throttled.error is not None and throttled.url == RELEASES_PAGE

    empty = check_for_update("0.1.4", http_get=lambda url: "[]")
    assert empty.available is False and empty.latest is None and empty.error is not None


RELEASE_NOTES_SCRIPT = Path(__file__).resolve().parents[1] / "build" / "release_notes.py"
"""Plan 1's half of T90. Absent on this branch alone; present once both are merged."""


def _release_notes_module() -> object:
    """Plan 1's script, loaded from its path, or a skip. It is a script, not a package."""
    if not RELEASE_NOTES_SCRIPT.exists():
        pytest.skip("plan 1's build/release_notes.py is not in this tree")
    spec = importlib.util.spec_from_file_location("t90_release_notes", RELEASE_NOTES_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_build_script_agrees_on_what_public_means() -> None:
    """One definition of `-Public`, or the release notes describe a different release."""
    if not RELEASE_NOTES_SCRIPT.exists():
        pytest.skip("plan 1's build/release_notes.py is not in this tree")
    assert 'r"^v(\\d+)\\.(\\d+)\\.(\\d+)-public$", re.IGNORECASE' in RELEASE_NOTES_SCRIPT.read_text(
        encoding="utf-8"
    )


def test_the_build_script_orders_versions_exactly_as_the_app_does() -> None:
    """Two implementations of one rule, compared tag by tag rather than by reading.

    The workflow picks the previous `-Public` tag with its own copy of this
    function, and the app decides what to offer with mine. A disagreement about
    a single tag means the release notes describe a range the update check
    never offers — and the pair that would show it is exactly the pair this
    rule exists for, `v0.8.65` and `v0.8.7`.

    Edge cases are in the list on purpose: a fourth number, a capital `V`, a
    suffix, a leading zero and a number too long to be one. Those are where two
    hand-written copies drift apart first.
    """
    module = _release_notes_module()
    their_key = module.version_key  # type: ignore[attr-defined]

    tags = [
        *THE_REAL_TAG_HISTORY,
        *(f"{tag}-Public" for tag in THE_REAL_TAG_HISTORY),
        "V0.8.7-Public",
        "v0.8.70-Public",
        "v0.8.7-Public-rc1",
        "v0.6.59Public",
        "v0.8.05",
        "v0.8.7.1",
        "v1.2.3.4-Public",
        "v0.8.9999999999",
        "v0.8." + "9" * 5000,
        "nightly",
        "",
    ]

    def theirs(tag: str) -> object:
        """Their answer, or the exception as a value — a raise IS a disagreement.

        Not left to propagate: the uncapped `int()` this rule now guards
        against raises here, and a test that dies inside the other
        implementation reports its traceback instead of the tag that caused it.
        """
        try:
            return their_key(tag)
        except Exception as exc:  # noqa: BLE001 - the point is that it must not
            return f"raised {type(exc).__name__}"

    disagreements = {tag: (theirs(tag), version_key(tag)) for tag in tags}
    assert {tag: pair for tag, pair in disagreements.items() if pair[0] != pair[1]} == {}


# ------------------------------------------ a tag_name is remote text of any shape


def test_the_stored_tag_is_the_matched_text_not_the_field() -> None:
    """`tag_name` is whatever the feed said; what this app keeps is what matched."""
    feed = json.dumps([release("  v1.2.0-Public\n", body="### Fixed\n- one.")])

    result = evaluate_feed(feed, "1.0.0-Public")

    assert result.latest == "v1.2.0-Public"
    assert result.notes[0].tag == "v1.2.0-Public"


def test_a_tag_of_two_megabytes_does_not_become_the_window_title() -> None:
    """Measured before the fix: `latest` 1,000,030 characters, 3.3 s on the GUI thread.

    A tag ending in a million newlines is 2 MB, well inside `MAX_FEED_BYTES`,
    and `latest` is what the bar says and what the dialog puts in its title
    bar (seventh cold review, 2026-09-21).
    """
    feed = json.dumps([release("v1.2.0-Public" + "\n" * 1000000, body="- one.")])

    started = time.monotonic()
    result = evaluate_feed(feed, "1.0.0-Public")
    elapsed = time.monotonic() - started

    assert result.latest == "v1.2.0-Public"
    assert result.notes[0].tag == "v1.2.0-Public"
    assert elapsed < 1.0, f"a 2 MB tag cost {elapsed:.2f}s"


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2.0-Public\nv9.9.9-Public",
        "v1.2.0-Public\r\nmore",
        "v1.2.0-Public more",
        "v1.2.0-Public more",
        "v1.2.0-Public\x00more",
        "v1.2.0-Public more",
        "v1.2.0-Public\n```\nx",
    ],
)
def test_a_tag_with_anything_after_it_is_not_a_public_release(tag: str) -> None:
    """`PUBLIC_TAG` is anchored at both ends, and the anchors are load-bearing."""
    assert is_public_tag(tag) is False
    assert public_tag(tag) is None
    assert evaluate_feed(json.dumps([release(tag, body="x")]), "1.0.0-Public").notes == ()
