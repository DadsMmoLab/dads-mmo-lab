"""The client folder: refuse what cannot extract, warn about what usually does not.

`validate()` is pure and returns preflight `Check`s, not questions. The scripts
asked "Continue anyway? (server WILL fail to load maps)" at every doubt; the
engine cannot ask, so each doubt is either a refusal (the folder, `Data/` or
the expansion's own MPQ is missing — nothing can be extracted from that) or a
warning (too few MPQs, no locale folder, the repack smell, a nearly full
drive — extraction usually still works, and the count gate in `extract.py` is
the real check). The build number is reported by nothing here: no script read
it either, and a number the folder cannot prove is not a fact.

Every refusal carries the sentence that gets the user out of it. A refusal is
the end of the road for this install, so one that only says what is wrong
leaves a person with a folder picker and no idea what to put in it.

Per-game data (`ClientSpec`) is the only thing that differs between the three
CMaNGOS clients, so this module holds no game literal — style-guide §3.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

from yulon.catalog.catalog import ClientSpec, MpqDepth
from yulon.catalog.preflight import Check

DATA_DIR = "Data"
"""Where every WoW client keeps its archives; its absence means "not a client"."""

CLIENT_CHECK = "the client folder"
REQUIRED_CHECK = "the client's expansion data"
MPQ_CHECK = "the client's archives"
LOCALE_CHECK = "the client's locale"
REPACK_CHECK = "the client's origin"
SPACE_CHECK = "free space next to the client"

PICK_THE_CLIENT = f"Pick the game folder that holds the {DATA_DIR} directory, then try again."
"""One sentence for every refusal that is really "that is not a client folder".

Said once so the three ways of picking the wrong thing — nothing, a file, a
path that has since been deleted — cannot drift into three different answers
to the same question.
"""

FreeBytes = Callable[[Path], int | None]
"""Free space on the volume holding a path, or `None` for "could not ask".

`None` is a third answer and never a small number: rounding it down would
invent a refusal on a machine with room, and rounding it up would hide the one
warning worth printing. Whoever reads it reports `unchecked`, the way
`preflight.evaluate()` does with every measurement it could not take.

This is vocabulary, not a guard. Nothing in this module reads the value —
`_warnings()` is still empty — so the alias asks Task I.2's space rule to be
written against three answers instead of two. The guard that stops `None` from
collapsing into zero has to be I.2's own test, next to the code that reads it.
"""


def validate(
    client_dir: Path | None, spec: ClientSpec, *, free_bytes: FreeBytes
) -> tuple[Check, ...]:
    """Every rule for one client folder, refusals first, in the order a user reads them.

    Returns as soon as a refusal makes the remaining rules meaningless (no
    folder, no `Data/`, not the right expansion): warning about the MPQ count
    of a folder that is not a client would bury the one line that matters.

    `free_bytes` is a seam and not `shutil.disk_usage` so the whole table is
    testable on a `tmp_path`; preflight passes its own `_free_bytes`.
    """
    if client_dir is None:
        return (
            Check(
                CLIENT_CHECK,
                "refuse",
                "no client folder was chosen, and this server's maps are extracted from one",
                PICK_THE_CLIENT,
            ),
        )
    if not client_dir.exists():
        return (
            Check(
                CLIENT_CHECK,
                "refuse",
                f"{client_dir} is not there",
                PICK_THE_CLIENT,
            ),
        )
    if not client_dir.is_dir():
        return (
            Check(
                CLIENT_CHECK,
                "refuse",
                f"{client_dir} is a file, not a folder",
                PICK_THE_CLIENT,
            ),
        )
    data = client_dir / DATA_DIR
    if not data.is_dir():
        return (
            Check(
                CLIENT_CHECK,
                "refuse",
                f"{client_dir} has no {DATA_DIR} directory, so it is not a game client",
                "Pick the folder the game itself is installed in, not a launcher, an installer "
                "or a backup.",
            ),
        )
    checks: list[Check] = [Check(CLIENT_CHECK, "pass", f"{client_dir} has a {DATA_DIR} directory")]
    if spec.required_file is not None:
        required = client_dir.joinpath(*spec.required_file.split("/"))
        if not required.is_file():
            checks.append(
                Check(
                    REQUIRED_CHECK,
                    "refuse",
                    f"{spec.required_file} is missing from {client_dir}, so this is either not "
                    "the expansion this server needs or an incomplete copy of it",
                    "Point the install at a complete client of the expansion this server runs, "
                    "then try again.",
                )
            )
            return tuple(checks)
        checks.append(Check(REQUIRED_CHECK, "pass", f"{spec.required_file} is there"))
    checks.extend(_warnings(client_dir, data, spec, free_bytes))
    return tuple(checks)


def _warnings(client_dir: Path, data: Path, spec: ClientSpec, free_bytes: FreeBytes) -> list[Check]:
    """The rules that warn (Task I.2 fills this in); nothing yet."""
    return []


def mpq_files(data: Path, depth: MpqDepth) -> tuple[Path, ...]:
    """Every `*.MPQ` (any case) under `data`, to `depth` levels or everywhere.

    Depth 1 is `Data/` itself, depth 2 adds `Data/<locale>/` — the shapes the
    three clients have, spelled as data so the module has no game in it.
    Raises OSError where the folder cannot be read; the caller decides what an
    unreadable client means.

    Both depths walk with `iterdir()` for that last sentence's sake.
    `Path.rglob()` swallows the OSError and answers with a short list, which
    would reach the caller as "too few archives" — the wrong sentence, and the
    only client whose spec asks for `recursive` is the one it would hide.
    """
    limit = depth if isinstance(depth, int) else None
    return tuple(
        sorted(
            path
            for path in _to_depth(data, limit)
            if path.is_file() and path.suffix.lower() == ".mpq"
        )
    )


def _to_depth(root: Path, depth: int | None) -> Iterator[Path]:
    """Everything under `root`, breadth-first, `depth` levels down or all of them."""
    level = [root]
    remaining = depth
    while level and remaining != 0:
        deeper: list[Path] = []
        for folder in level:
            for entry in folder.iterdir():
                yield entry
                if entry.is_dir():
                    deeper.append(entry)
        level = deeper
        if remaining is not None:
            remaining -= 1


def locale_dirs(data: Path) -> tuple[Path, ...]:
    """Subfolders of `Data/` holding an MPQ — `enUS`, `deDE`; what a repack strips."""
    return tuple(
        sorted(
            folder
            for folder in data.iterdir()
            if folder.is_dir()
            and any(f.is_file() and f.suffix.lower() == ".mpq" for f in folder.iterdir())
        )
    )
