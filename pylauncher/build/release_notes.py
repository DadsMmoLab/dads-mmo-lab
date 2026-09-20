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


def _triple(tag: str) -> tuple[int, int, int] | None:
    match = _ANY_VERSION.match(tag.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def pick_previous(tags: Iterable[str], tag: str) -> str | None:
    """The highest-versioned -Public tag whose version is below `tag`'s, or None."""
    mine = _triple(tag)
    if mine is None:
        return None
    below = [
        (version, t)
        for t in tags
        if PUBLIC_TAG.match(t.strip()) and (version := _triple(t)) is not None and version < mine
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
