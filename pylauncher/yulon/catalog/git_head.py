"""The commit a checkout is on, read off `.git` with no git run (moved out of `native.py`, T230).

Pure and import-light, so both `native` and `build_context` can use it: `native`
imports `build_context`, so `build_context` cannot import `native`.

It follows a detached HEAD (the commit itself), a branch's loose ref and a
branch's line in `packed-refs`, in git's own order (loose first). It does not
follow a ref that names another ref, a reftable or a worktree's `commondir`; it
answers None there, which a caller reads as "cannot say".
"""

from __future__ import annotations

import re
from pathlib import Path

_COMMIT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def _a_commit(text: str) -> str | None:
    """`text` if it is a full commit id (SHA-1 or SHA-256), else None."""
    return text if _COMMIT_ID.fullmatch(text) else None


def resolve_head(gitdir: Path, head_text: str) -> str | None:
    """The commit `head_text` (the text of `gitdir/HEAD`) names; None = cannot say.

    Raises OSError only if a ref file exists and cannot be read.
    """
    head = head_text.strip()
    if not head.startswith("ref: "):
        return _a_commit(head)
    ref = head[len("ref: ") :]
    loose = gitdir.joinpath(*ref.split("/"))
    if loose.is_file():
        # A loose ref that itself says `ref: …` is not a commit: unknown, not a
        # refusal (scoped re-review of c5bf1b67).
        return _a_commit(loose.read_text(encoding="utf-8").strip())
    packed = gitdir / "packed-refs"
    if not packed.is_file():
        return None
    for line in packed.read_text(encoding="utf-8").splitlines():
        sha, _, name = line.partition(" ")
        if name == ref:
            return _a_commit(sha)
    return None


def read_head_file(dest: Path) -> str | None:
    """The commit a checkout is on, read off `.git/HEAD` with no git run; None = cannot say.

    For a Start, which must not wait on a containerised git: a detached HEAD
    (what `checkout --detach` leaves) holds the sha itself, and a branch is
    resolved through its loose ref or `packed-refs`.
    """
    gitdir = dest / ".git"
    try:
        return resolve_head(gitdir, (gitdir / "HEAD").read_text(encoding="utf-8"))
    except OSError:
        return None
