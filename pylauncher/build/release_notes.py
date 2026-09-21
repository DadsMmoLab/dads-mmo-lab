"""The GitHub release body: what CHANGELOG.md gained since the previous -Public tag.

Run by `release.yml` after the artifacts are built. The person who pushes the tag
does nothing new: nobody retitles `## Unreleased`, and a changelog this script
cannot read, or that gained nothing, leaves the body to GitHub's generated notes.
It exits 0 whatever happens - a release is never refused over its notes.

Stdlib only, and no import from `yulon`: it runs on a bare checkout.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from fractions import Fraction
from pathlib import Path

PUBLIC_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-public$", re.IGNORECASE)
_ANY_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
_DEFAULT_HEADING = "New"

RunGit = Callable[[list[str]], str]


def parse_changelog(text: str) -> list[tuple[str, str]]:
    """Every bullet in the file as (heading, bullet), in file order.

    A `## ` line resets the heading to "New"; a `### ` line sets it. A bullet is
    a line starting `- `; indented lines directly after it are its continuation.
    Anything else (prose, blank lines) is not an entry and ends the bullet.
    """
    entries: list[tuple[str, str]] = []
    heading = _DEFAULT_HEADING
    bullet: list[str] | None = None

    def close() -> None:
        nonlocal bullet
        if bullet is not None:
            entries.append((heading, "\n".join(bullet)))
            bullet = None

    for line in text.splitlines():
        if line.startswith("### "):
            close()
            heading = line[4:].strip()
        elif line.startswith("## "):
            close()
            heading = _DEFAULT_HEADING
        elif line.startswith("- "):
            close()
            bullet = [line.rstrip()]
        elif bullet is not None and line.startswith((" ", "\t")) and line.strip():
            bullet.append(line.rstrip())
        else:
            close()
    close()
    return entries


def new_entries(old: str, new: str) -> str:
    """Markdown of the bullets `new` has and `old` does not, under their headings."""
    seen = {bullet for _, bullet in parse_changelog(old)}
    grouped: dict[str, list[str]] = {}
    for heading, bullet in parse_changelog(new):
        if bullet not in seen:
            grouped.setdefault(heading, []).append(bullet)
    blocks = [
        f"### {heading}\n" + "\n".join(bullets) + "\n" for heading, bullets in grouped.items()
    ]
    return "\n".join(blocks)


def version_key(tag: str) -> tuple[int, int, Fraction] | None:
    """How Yu'lon versions sort: the LAST number is a decimal fraction.

    Owner's decision, 2026-09-21, and it is what the tag history has always
    done. `v0.8.7-Public` was cut on 2026-09-19, six days AFTER
    `v0.8.65-Public`; the whole line reads 0.6.5, 0.6.51 ... 0.6.59, 0.8.0,
    0.8.4, 0.8.5, 0.8.6, 0.8.65, 0.8.7, with the fork's .66 .67 .68 .69 test
    tags sitting between .65 and .7. Read as integers, 65 > 7, so the notes for
    the release after 0.8.7 would have been measured against 0.8.65 and would
    have re-published everything 0.8.7 already announced.

    So major and minor are whole numbers, and the last number is a fraction of
    its own digits: "65" is 65/100, "7" is 7/10, "0" is 0. That makes
    .6 < .65 < .66 < .69 < .7, and .7 and .70 the same version.

    `Fraction`, not `float`: the comparison is exact, and two tags that mean
    the same version compare equal rather than nearly equal.

    KNOWN COST, not fixed: 0.8.10 would order BELOW 0.8.9, because 10/100 is
    less than 9/10. No tag in this repository has ever been written that way,
    and the scheme the owner picked is the one the tags are in.
    """
    match = _ANY_VERSION.match(tag.strip())
    if match is None:
        return None
    last = match[3]
    return (int(match[1]), int(match[2]), Fraction(int(last), 10 ** len(last)))


def pick_previous(tags: Iterable[str], tag: str) -> str | None:
    """The highest-versioned -Public tag whose version is below `tag`'s, or None."""
    mine = version_key(tag)
    if mine is None:
        return None
    # Strictly below: `v0.8.70-Public` and `v0.8.7-Public` are one version, so
    # neither is the other's previous release.
    below = [
        (version, t)
        for t in tags
        if PUBLIC_TAG.match(t.strip())
        and (version := version_key(t)) is not None
        and version < mine
    ]
    return max(below)[1].strip() if below else None


def _git(argv: list[str]) -> str:
    # `errors="replace"`: a changelog blob is whatever somebody committed, and
    # one byte of latin-1 in it (an accented name, a dash pasted from a mail
    # client) would otherwise raise UnicodeDecodeError inside `subprocess`
    # itself - before this function's own error handling, and out through
    # `main`'s `except OSError`, which a ValueError is not. The entry it
    # appears in still reads; one character of it becomes U+FFFD.
    done = subprocess.run(
        ["git", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode != 0:
        raise OSError(done.stderr.strip() or f"git {argv[0]} exited {done.returncode}")
    return done.stdout


def main(argv: Sequence[str] | None = None, *, run_git: RunGit = _git) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    notes = ""
    try:
        new = run_git(["show", f"{args.tag}:CHANGELOG.md"])
        previous = pick_previous(run_git(["tag", "--list", "v*"]).splitlines(), args.tag)
        old = ""
        if previous is not None:
            try:
                old = run_git(["show", f"{previous}:CHANGELOG.md"])
            except OSError:
                old = ""
        notes = new_entries(old, new)
        print(f"release notes: {args.tag} against {previous or 'nothing'}: {len(notes)} characters")
    # `Exception`, not `OSError`. The promise at the top of this file is that a
    # release is never refused over its notes, and an `except OSError` keeps it
    # only for the failures that were thought of: a `git` this script cannot
    # run, and a ref it cannot read. Anything else - a decode, a regex, a
    # `MemoryError` on a changelog somebody grew to a gigabyte - came out as a
    # traceback and a red step on a release whose artifacts were already
    # published. `BaseException` is deliberately NOT caught: a cancelled run
    # must stop rather than publish an empty body.
    except Exception as exc:  # noqa: BLE001 - see above; the release outranks the notes
        print(f"release notes skipped, GitHub's generated notes stay: {exc!r}", file=sys.stderr)
    args.out.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
