"""Refuse a `-Public` release tag that does not sort above the previous public one.

Run by `release.yml` before anything is built. The update check offers whatever
sorts HIGHEST by `release_notes.version_key`, so a public tag cut at or below
the newest one either never reaches a player (a re-cut, a lower line) or hides
the real release behind a wrong one (a stray `v0.9.2-Public` for `v0.9.20`).
Nothing else stopped such a tag before this (T669).

A ref that is not a `-Public` tag (a branch on a manual run, a `-fixtest` tag)
is nothing to check and exits 0: only public tags are ever offered. Exit 1
means "refused", and so does a tag list that cannot be read - failing open
would wave through the very tag this exists to stop.

Stdlib only, and no import from `yulon`: it runs on a bare checkout. The order
itself is `release_notes.version_key`, the one copy of the rule this directory
carries.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence

from release_notes import PUBLIC_TAG, version_key

RunGit = Callable[[list[str]], str]


def tag_order_problem(tags: Iterable[str], tag: str) -> str | None:
    """Why `tag` may not be released as the newest public tag, or None if it may.

    `tags` is every tag in the repository, `tag` among them. The tag is compared
    with every OTHER public tag that the app can read, and must sort strictly
    above all of them: equal is refused too (`v0.8.7` and `v0.8.70` are one
    version, and the update check would see no update). Non-public tags are
    ignored, since the update check never offers them (and `tag` itself, if it
    is not public, has nothing to check). Re-running the newest tag passes: it
    is the one tag left out of its own comparison.
    """
    if not PUBLIC_TAG.match(tag.strip()):
        return None
    mine = version_key(tag)
    if mine is None:
        return (
            f"{tag} is a public tag the app cannot read as a version "
            "(three numbers of at most nine digits), so the update check would drop it"
        )
    rivals = [
        (version, other.strip())
        for other in tags
        if other.strip() != tag.strip()
        and PUBLIC_TAG.match(other.strip())
        and (version := version_key(other)) is not None
    ]
    above = sorted((r for r in rivals if r[0] > mine), reverse=True)
    same = sorted(r for r in rivals if r[0] == mine)
    if not above and not same:
        return None
    if above:
        why = f"{tag} sorts below the existing {_name_tags([t for _, t in above])}"
        if same:
            why += f", and is the same version as the existing {_name_tags([t for _, t in same])}"
    else:
        why = f"{tag} is the same version as the existing {_name_tags([t for _, t in same])}"
    return (
        f"{why} under the hybrid version rule, so the update check would not offer it "
        "as the newest release (from 0.9 on the last number counts as a whole number: "
        "0.9.9 < 0.9.10 < 0.9.16; below 0.9 it is a decimal). "
        "Check the number for a typo before tagging again."
    )


_NAMED = 5


def _name_tags(tags: list[str]) -> str:
    """`a`, `a and b`, `a, b and c`; past five, the first five `and N more public tags`."""
    if len(tags) > _NAMED:
        return f"{', '.join(tags[:_NAMED])} and {len(tags) - _NAMED} more public tags"
    if len(tags) == 1:
        return tags[0]
    return f"{', '.join(tags[:-1])} and {tags[-1]}"


def _git(argv: list[str]) -> str:
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
    args = parser.parse_args(argv)
    tag = args.tag.strip()
    if not PUBLIC_TAG.match(tag):
        print(f"{tag!r} is not a -Public release tag: no order to check")
        return 0
    try:
        tags = run_git(["tag", "--list", "v*"]).splitlines()
    except OSError as exc:
        print(f"cannot list the tags to check {tag} against: {exc}", file=sys.stderr)
        return 1
    problem = tag_order_problem(tags, tag)
    if problem is not None:
        print(f"::error title=Tag out of order::{problem}", file=sys.stderr)
        return 1
    print(f"{tag} sorts above every other public tag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
