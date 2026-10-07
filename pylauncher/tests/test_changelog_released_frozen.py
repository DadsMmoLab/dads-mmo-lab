"""A released CHANGELOG section never changes (T535).

`build/release_notes.py` cuts a release body from what the tag gained: it skips
every section at or below the previous tag. So a line added later under an
already-released `## vX-Public` heading is never published. This guard fails
when a released section's bullets change.

CI checks out shallow with no tags (`actions/checkout@v4` defaults), so the
guard cannot lean on `git show <tag>:CHANGELOG.md` there. It pins each released
section as (bullet count, sha256 of its sorted bullets). When git has the tags
(a developer's clone) a second test re-derives each section from the tag and
checks the pin against it.

THE RELEASE CUT UPDATES `PINNED`: the commit that renames `## Unreleased` to
`## vX.Y.Z-Public` adds that version's pin. Print the new line with
`python -m tests.test_changelog_released_frozen vX.Y.Z-Public`.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))

import release_notes as rn  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CHANGELOG = REPO / "CHANGELOG.md"

PINNED: dict[str, tuple[int, str]] = {
    "v0.9.14-Public": (5, "fe4bbd2f1356ab73845c8d87ad124f7cb7d7ab47ff235fa8170e7c6bc9d1b27a"),
    "v0.9.13-Public": (145, "0ec8b19c74fbeb3209069b7bc05a57f4a115b9386f63556dd39a403714c52336"),
    "v0.8.90-Public": (35, "9eeb32ab1f77cc8f33d90dca36350cb46f39f33c4d7f84c2b619254b4fd4c2be"),
    "v0.8.7-Public": (8, "39bd250cc89a516f1bc0bc1f224a484fea3235c66c70348814f7f59a239757b8"),
    "v0.8.4-Public": (8, "5519d9a553942f00b3f31aadd6139abd949b5acc6133c2e593c841cec7ebdb2f"),
    "v0.8.0-Public": (18, "bf6cb4ebf594a7d90c6029bfafc8311a0ec1779e0ecdb361630e6cd508e9f6ec"),
}


def released_bullets(text: str) -> dict[str, list[str]]:
    """Each `## vX-Public` section's bullets, keyed by the tag name."""
    found: dict[str, list[str]] = defaultdict(list)
    for section, _, bullet in rn.parse_sections(text):
        name = section.split(" ")[0]
        if name.startswith("v") and name.endswith("-Public"):
            found[name].append(bullet)
    return dict(found)


def pin(bullets: list[str]) -> tuple[int, str]:
    digest = hashlib.sha256("\n".join(sorted(bullets)).encode()).hexdigest()
    return (len(bullets), digest)


def drift(text: str) -> list[str]:
    """Every released section whose bullets differ from the pin, one sentence each."""
    seen = released_bullets(text)
    found: list[str] = []
    for tag, pinned in PINNED.items():
        if tag not in seen:
            found.append(f"{tag}: the section is gone")
        elif pin(seen[tag]) != pinned:
            found.append(f"{tag}: bullets changed ({len(seen[tag])} now, {pinned[0]} pinned)")
    for tag in seen:
        if tag not in PINNED:
            found.append(f"{tag}: released but not pinned; add it to PINNED")
    return found


def test_released_sections_are_unchanged() -> None:
    assert drift(CHANGELOG.read_text(encoding="utf-8")) == []


def test_a_line_added_under_a_released_heading_is_caught() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    heading = next(x for x in text.splitlines() if x.startswith("## v0.9.14-Public"))
    edited = text.replace(heading, heading + "\n\n### New\n\n- A line that would never ship.", 1)
    assert drift(edited) == ["v0.9.14-Public: bullets changed (6 now, 5 pinned)"]


def test_an_unpinned_release_heading_is_caught() -> None:
    real = CHANGELOG.read_text(encoding="utf-8")
    text = "## v9.9.9-Public — 2030-01-01\n\n### New\n\n- x\n" + real
    assert "v9.9.9-Public: released but not pinned; add it to PINNED" in drift(text)


def _tag_text(tag: str) -> str | None:
    done = subprocess.run(
        ["git", "show", f"{tag}:CHANGELOG.md"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return done.stdout if done.returncode == 0 else None


TAG_DERIVED = ("v0.9.14-Public", "v0.9.13-Public")
"""Only these were cut from their tag (#361). Older sections were reworded to the
plain style (T360) after their tags, so their tag text differs; they stay pinned only."""


@pytest.mark.parametrize("tag", TAG_DERIVED)
def test_the_pin_matches_what_the_tag_gained(tag: str) -> None:
    """Needs the tags: skipped in CI's shallow checkout, run in a developer's clone."""
    tagged = _tag_text(tag)
    if tagged is None:
        pytest.skip(f"{tag} is not in this checkout")
    current = released_bullets(CHANGELOG.read_text(encoding="utf-8"))
    names = list(current)
    older = Counter(b for n in names[names.index(tag) + 1 :] for b in current[n])
    gained = Counter(b for _, _, b in rn.parse_sections(tagged)) - older
    assert sorted(gained.elements()) == sorted(current[tag])


if __name__ == "__main__":
    for name in sys.argv[1:]:
        count, digest = pin(released_bullets(CHANGELOG.read_text(encoding="utf-8"))[name])
        print(f'    "{name}": ({count}, "{digest}"),')
