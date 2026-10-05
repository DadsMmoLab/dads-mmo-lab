"""CHANGELOG.md's player lines stay short and plain (T360).

Players read these lines in the update dialog: the release body is cut from
this file by `build/release_notes.py`. The owner's rule of 2026-10-05: under
each release, only `### New`, `### Fixed` and `### Changed`; one plain line per
change, at most about 120 characters, saying what the player gets. Ticket ids,
proof, test notes and commands go in the pull request, not here.

Checked for `## Unreleased` and every release written in that style, which is
every section above `## v0.6.59Public` (that one predates the log and is left
as it was).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
FIRST_UNGUARDED = "## v0.6.59Public"
HEADINGS = ("### New", "### Fixed", "### Changed")
HARD_CAP = 140
"""The cap a line fails at. The target is about 120; this leaves room above it."""

_TICKET = re.compile(r"\bT\d{2,4}\b")


def _sections(text: str) -> dict[str, list[str]]:
    """The guarded `## ` sections, each as its lines after the heading."""
    head = text[: text.index(FIRST_UNGUARDED)]
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in head.splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line, [])
        elif current is not None:
            current.append(line)
    return sections


def problems(text: str) -> list[str]:
    """Every way `text`'s guarded sections break the rule, one sentence each."""
    found: list[str] = []
    sections = _sections(text)
    if "## Unreleased" not in sections:
        found.append("there is no '## Unreleased' section")
    for title, lines in sections.items():
        seen: list[str] = []
        heading: str | None = None
        for line in lines:
            if not line.strip():
                continue
            where = f"{title}: {line[:70]!r}"
            if line.startswith("#"):
                if line not in HEADINGS:
                    found.append(f"{where}: only {', '.join(HEADINGS)} are allowed")
                elif line in seen:
                    found.append(f"{where}: this heading appears twice")
                else:
                    seen.append(line)
                heading = line
                continue
            if not line.startswith("- "):
                found.append(f"{where}: not a bullet (a line break inside one, or prose)")
                continue
            if heading is None:
                found.append(f"{where}: a bullet before the first heading")
            if len(line) > HARD_CAP:
                found.append(f"{where}: {len(line)} characters, the cap is {HARD_CAP}")
            if _TICKET.search(line):
                found.append(f"{where}: names a ticket")
            if "`" in line:
                found.append(f"{where}: has a backtick")
            if "LIVE-PROOF-PENDING" in line:
                found.append(f"{where}: says LIVE-PROOF-PENDING")
        order = [h for h in HEADINGS if h in seen]
        if seen != order:
            found.append(f"{title}: headings must come in the order {', '.join(HEADINGS)}")
    return found


def test_the_changelog_follows_the_player_line_rule() -> None:
    assert problems(CHANGELOG.read_text(encoding="utf-8")) == []


GOOD = """# Changelog

## Unreleased

### New
- A thing you can now do.

### Fixed
- A thing that no longer breaks.

## v0.6.59Public — 2026-08-29
"""


def test_a_good_changelog_passes() -> None:
    assert problems(GOOD) == []


@pytest.mark.parametrize(
    ("bad", "says"),
    [
        ("### Known issues\n- Something.", "only"),
        ("- " + "x" * HARD_CAP, "characters"),
        ("- A thing is fixed (T123).", "ticket"),
        ("- Run `docker ps` to see it.", "backtick"),
        ("- A thing; LIVE-PROOF-PENDING.", "LIVE-PROOF-PENDING"),
        ("- A thing that goes on\n  onto a second line.", "not a bullet"),
        ("Some prose.", "not a bullet"),
    ],
)
def test_each_rule_catches_its_own_break(bad: str, says: str) -> None:
    text = GOOD.replace("- A thing you can now do.", bad)
    assert any(says in problem for problem in problems(text)), problems(text)


def test_a_bullet_straight_under_the_release_heading_is_refused() -> None:
    text = GOOD.replace("### New\n", "")
    assert any("before the first heading" in p for p in problems(text))


def test_headings_out_of_order_are_refused() -> None:
    text = GOOD.replace("### New", "### Changed").replace("### Fixed", "### New")
    assert any("order" in p for p in problems(text))


def test_the_rule_written_at_the_top_of_the_file_is_not_a_line_players_read() -> None:
    """The how-to comment above `## Unreleased` must never reach a release body.

    The cutter takes bullets and `###` headings; a comment line that started
    with `- ` or `### ` would be published. GitHub hides the comment itself.
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    preamble = text[: text.index("\n## Unreleased")]
    assert "<!--" in preamble and "-->" in preamble
    for line in preamble.splitlines():
        assert not line.startswith(("- ", "## ", "### ")), line
