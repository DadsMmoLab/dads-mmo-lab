"""Does a move between two commits of a source go FORWARD in its history? (T632, T630)

A Return that moves forward over an upstream squash must not read the files the squash
deleted as "newer than the target"; one that moves back must count every file the running
commit added since, whatever the file is named. So the direction has to be known, and a
name never says it.

Git answers first (`Seams.is_ancestor`: true, false, or None when a shallow checkout's
grafts leave it unable to tell, which is how every depth-1 source looks). GitHub's compare
answers when git cannot: `behind_by == 0` is a forward (or identical) move. When neither
can, the caller refuses and says why; it never guesses a direction.
"""

from __future__ import annotations

from pathlib import Path

from yulon.catalog import upstream
from yulon.catalog.native import Seams


def moves_forward(
    seams: Seams, repo: str, dest: Path, old: str, new: str
) -> tuple[bool | None, str]:
    """`(forward, why)`: True / False when known; None with the reason when neither can tell."""
    local = seams.is_ancestor(dest, old, new)
    if local is not None:
        return local, ""
    said = upstream.compare(repo, old, new, get=seams.upstream_get)
    if said is None:
        return None, (
            f"git could not show whether {old[:7]} is behind {new[:7]} (a shallow checkout "
            f"keeps no history to walk) and GitHub did not answer for {repo}"
        )
    return said.behind == 0, ""
