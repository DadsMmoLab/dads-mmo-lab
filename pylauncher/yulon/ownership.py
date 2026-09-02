"""Whose folder is this? One answer shared by both engines that can destroy work.

This enum was written for the native install engine (`catalog/native.py`) after
a corrupt state file made it MORE confident a folder was its own than a missing
file did. It lives here, in a module with no dependencies, because the module
applier (`apply.py`) reaches the same fork on a `modules/<id>` clone: it too
can `git reset --hard` or `shutil.rmtree` a directory, and it too must be able
to say "I cannot tell" without that meaning "go ahead".

The two engines answer the question from different evidence — a
`.yulon-install.json` at the server dir there, a `.yulon-clone.json` inside the
clone here — and neither evidence shape fits the other, so only the answer is
shared. That is the part that must not be re-invented with two values.
"""

from __future__ import annotations

from enum import Enum


class Ownership(Enum):
    """Whose folder is this? Three answers, because a bool has nowhere to put the third.

    `UNCLAIMED` — no record. Nobody has claimed the folder; whatever else is in
    it is judged by the guards that look at the disk.

    `OWNED` — a record that PARSED and that names this exact folder and this
    exact item. Only this is ownership.

    `UNKNOWN` — a record is there and could not be turned into any of that:
    truncated by a crash mid-write, damaged by hand, written by a version this
    one cannot read, or an unrelated file that happens to sit at the reserved
    name. This is the case where the app knows LEAST, and it must never be the
    case where it acts most freely. It fails closed everywhere it is reached;
    see `catalog.native.StagedInstaller.claimed_this_folder()` for what that
    bought, and `apply.read_clone_claim()` for the second place it buys it.
    """

    UNCLAIMED = "unclaimed"
    OWNED = "owned"
    UNKNOWN = "unknown"
