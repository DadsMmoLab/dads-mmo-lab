"""Nothing committed here carries a live credential (added 2026-09-07).

The defect this exists for: two gate captures from 2026-09-04 carried the two
CMaNGOS installs' **generated MySQL root passwords**, in a line the worldserver
itself printed when it could not reach its database —

    Cannot connect to world database tbc-db;3306;mangos;<password>;mangos

— and were merged upstream with #143 before GitGuardian reported them. Nobody
read those files line by line, which is the point: a gate transcript is written
by a machine and skimmed by a person, and that is exactly where a secret hides.

The shape is knowable, which is what makes this checkable. `resolve_secrets()`
mints a per-install password as `<game>-<token_hex(8)>`, so anything matching
`<word>-<16 hex>` in a committed page or capture is either one of those or an
illustration of one. The illustrations are listed below with their reason, and
anything else fails.

This is deliberately not a general secret scanner. It knows one shape — the one
this project generates — because a guard that recognises what we produce is
worth more than one that recognises everything badly.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_GENERATED_PASSWORD = re.compile(r"\b([a-z]+)-([0-9a-f]{16})\b")

ILLUSTRATIONS = {
    # value -> why it is not a secret. Written out rather than derived: the map
    # IS the claim, and one computed from the files would agree with whatever
    # the files happened to contain. A new entry needs a reason a person wrote.
    "0123456789abcdef": "hex digits in order, in prose explaining the shape",
    "fedcba9876543210": "the same, backwards, in a Dockerfile test fixture",
    "0a1b2c3d4e5f6a7b": "a counting pattern used in bug-checklist prose",
    "1a2b3c4d5e6f7a8b": "the same pattern, in controller-view and sqlplan fixtures",
    "deadbeefcafe1234": "`deadbeef` and `cafe` — words, in a cmangos docstring example",
    "0000000000000000": "sixteen zeros — the dummy the 8.9b tests write into a .db_password",
}


def _committable(repo: Path = REPO) -> list[Path]:
    """Every file git would publish: tracked, staged, or new and not ignored.

    Asked of git rather than walked from a list of folders. Until 2026-09-14 this
    searched `pyplan/` and `pylauncher/` only, and that was the whole tree the
    evidence lived in; then the gate captures moved to the gitignored `.notes/`,
    and a `git add -f` of one of them would have been committed unseen. Git's
    own list follows a file wherever it is added from, force-added ones included.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return sorted({repo / name for name in listed.split("\0") if name})


def _candidates(repo: Path = REPO) -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for path in _committable(repo):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in _GENERATED_PASSWORD.finditer(line):
                if match.group(2) in ILLUSTRATIONS:
                    continue
                found.append((path.relative_to(repo), number, match.group(0)))
    return found


def test_no_committed_page_or_capture_carries_a_generated_password() -> None:
    """`<game>-<16 hex>` is the shape this project mints; none may be committed."""
    found = _candidates()

    assert not found, "a generated password is committed:\n" + "\n".join(
        f"  {path}:{number}  {value}" for path, number, value in found
    )


def test_the_guard_can_actually_see_one() -> None:
    """A guard nobody has watched fail is a guard nobody should trust.

    The pattern is exercised against the exact line that leaked — with the value
    assembled from halves, so that proving the guard works does not re-commit
    the thing it is guarding against, and so this file passes its own scan.
    """
    value = "tbc-" + "faf2e5c4" + "5f363783"
    leaked = f"Cannot connect to world database tbc-db;3306;mangos;{value};mangos"

    match = _GENERATED_PASSWORD.search(leaked)

    assert match is not None
    assert match.group(0) == value
    assert match.group(2) not in ILLUSTRATIONS


def test_every_illustration_says_why_it_is_not_a_secret() -> None:
    """The bar for the allow-list: a reason a person wrote, not a shape a script matched.

    This cannot decide whether a hex string is random — that is the whole
    difficulty — so it does not pretend to. What it can insist on is that
    somebody looked at each one and said why, which is the same rule
    `test_spine.py`'s folder-listing map uses.
    """
    for value, reason in ILLUSTRATIONS.items():
        assert reason.strip(), f"{value} is allowed with no reason given"
        assert len(reason) > 20, f"{value}'s reason is too short to be one: {reason!r}"


def test_the_allow_list_is_not_a_place_to_put_a_real_one() -> None:
    """Every entry must actually appear in the tree, as an illustration does.

    An allow-list that accumulates values nothing uses is how a real secret gets
    parked in one: the entry outlives the file it was written for, and nobody
    can tell the difference afterwards.
    """
    haystack = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in _committable()
        if path.is_file()
    )
    unused = [value for value in ILLUSTRATIONS if value not in haystack]

    assert not unused, f"these are allowed but appear nowhere: {unused}"


def test_the_scan_reaches_every_part_of_the_tree_git_would_publish() -> None:
    """Not vacuous: the listing covers both halves of the project and more than a handful."""
    listed = {path.relative_to(REPO).parts[0] for path in _committable()}
    assert {"pyplan", "pylauncher", ".github"} <= listed, sorted(listed)
    assert len(_committable()) > 300, len(_committable())


def test_a_force_added_file_under_an_ignored_folder_is_scanned(tmp_path: Path) -> None:
    """The route the 2026-09-14 move opened: `.notes/` is ignored, `git add -f` still commits."""

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init", "-q")
    (tmp_path / ".gitignore").write_text(".notes/\n", encoding="utf-8")
    capture = tmp_path / ".notes" / "gates" / "press.log"
    capture.parent.mkdir(parents=True)
    value = "tbc-" + "faf2e5c4" + "5f363783"
    capture.write_text(f"tbc-db;3306;mangos;{value};mangos\n", encoding="utf-8")
    (tmp_path / ".notes" / "unadded.log").write_text(f"{value}\n", encoding="utf-8")

    assert _candidates(tmp_path) == [], "an ignored, unadded file is not publishable"
    git("add", "-f", ".notes/gates/press.log")
    assert _candidates(tmp_path) == [(Path(".notes/gates/press.log"), 1, value)]
