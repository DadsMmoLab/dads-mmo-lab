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
from yulon.catalog.preflight import GIB, Check
from yulon.log import get_logger

logger = get_logger(__name__)

DATA_DIR = "Data"
"""Where every WoW client keeps its archives; its absence means "not a client"."""

CLIENT_CHECK = "the client folder"
REQUIRED_CHECK = "the client's expansion data"
MPQ_CHECK = "the client's archives"
LOCALE_CHECK = "the client's locale"
REPACK_CHECK = "the client's origin"
SPACE_CHECK = "free space next to the client"

REPACK_FILE = "realmlist.wtf"
"""A pre-configured client ships one at the root; a retail install keeps it under Data/<locale>/."""

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

`_space_check()` is the one reader, and it tests `free is None` rather than
`not free`: a drive with nothing left on it answered the question, and a
0 GB warning is the answer a user can act on. `None` never reaches the
comparison at all.
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
    """The rules that warn. Extraction usually survives every one of them, so none refuses.

    In reading order: the archive count is the rule that most often decides an
    install, the locale and repack rules explain a count that looks fine but is
    not, and free space is about the machine rather than the folder.

    Each rule has THREE answers. "Could not look" is `unchecked` — the verdict
    `preflight.evaluate()` gives every measurement it could not take — and it
    is never rounded to a pass or to the smallest number. An unreadable client
    reported as "too few archives" sends a user to re-download a folder that
    was intact, and the permission problem is still there when they come back.
    """
    locales = _locales(data)
    checks = [_mpq_check(data, spec)]
    if spec.locale_mpq_required:
        checks.append(_locale_check(data, locales))
    checks.append(_repack_check(client_dir, data, locales))
    checks.append(_space_check(client_dir, spec, free_bytes))
    return checks


def _locales(data: Path) -> tuple[Path, ...] | None:
    """The locale folders, or `None` for "the folder would not be listed".

    Read once and handed to both rules that want it, so an unreadable `Data/`
    cannot come out `unchecked` in one row and a confident `pass` in the next.
    Two rules calling `locale_dirs()` separately is how "could not read this
    folder" turns into "this folder has no locales in it, so it is a repack".
    """
    try:
        return locale_dirs(data)
    except OSError as exc:
        logger.info(f"could not list the folders under {data}: {exc}")
        return None


def _mpq_check(data: Path, spec: ClientSpec) -> Check:
    """Fewer archives than the expansion ships is the incomplete-download smell.

    A warning, not a refusal: a client with the right expansion MPQ but fewer
    patches extracts, and the `produces` counts in `extract.py` are what prove
    it did.

    `mpq_files()` RAISES where the folder cannot be walked, precisely so this
    rule can tell "two archives" from "would not say" (I.1 chose `iterdir()`
    over `rglob()` for it). Catching that and answering with the count sentence
    anyway would throw the distinction away in the one place it is visible.
    """
    where = _where(data, spec.mpq_depth)
    try:
        found = mpq_files(data, spec.mpq_depth)
    except OSError as exc:
        logger.info(f"could not list the archives under {data}: {exc}")
        return Check(
            MPQ_CHECK,
            "unchecked",
            f"the archives {where} could not be listed — that is not a pass",
            "Check that the folder opens for you; if it does not, extraction will find "
            "nothing in it either.",
        )
    if len(found) < spec.min_mpq:
        return Check(
            MPQ_CHECK,
            "warn",
            f"{len(found)} MPQ archives {where}; a complete client has at least {spec.min_mpq}",
            "Finish or repeat the client download — if extraction reports too few maps, "
            "this is why.",
        )
    return Check(MPQ_CHECK, "pass", f"{len(found)} MPQ archives {where}")


def _where(data: Path, depth: MpqDepth) -> str:
    """Where the count looked, in the words of the spec that asked for it.

    Said in the detail because the number alone is useless: "4 archives" sends
    a user hunting through a client that is not short of anything, when the
    rule simply never opened the locale folder holding the other two.
    """
    if not isinstance(depth, int):
        return f"anywhere under {data}"
    if depth == 1:
        return f"directly in {data}"
    return f"in {data}, up to {depth} folders deep"


def _locale_check(data: Path, locales: tuple[Path, ...] | None) -> Check:
    """This expansion keeps its DBC data in `Data/<locale>/`; a stripped copy has none."""
    if locales is None:
        return Check(
            LOCALE_CHECK,
            "unchecked",
            f"the folders under {data} could not be listed, so whether this client has its "
            "locale archives is unknown — that is not a pass",
            "Check that the folder opens for you, then start the install again.",
        )
    if not locales:
        return Check(
            LOCALE_CHECK,
            "warn",
            f"no locale folder holding archives under {data} (enUS, deDE, ...), and this "
            "expansion keeps its DBC data in one",
            "Reinstall the client from a complete download if extraction comes up short on "
            "DBC files.",
        )
    names = ", ".join(folder.name for folder in locales)
    return Check(LOCALE_CHECK, "pass", f"locale archives in {names}")


def _repack_check(client_dir: Path, data: Path, locales: tuple[Path, ...] | None) -> Check:
    """`realmlist.wtf` at the root and no locale folder: a stripped, pre-pointed repack.

    Such clients have had their archives rearranged and extract incompletely or
    not at all. The heuristic is exactly what the scripts eyeballed; it warns
    rather than refuses because a hand-moved realmlist on a full client is also
    a thing people do.

    Both halves are required, and the file is read first: with no `realmlist.wtf`
    at the root the answer is settled by evidence that WAS obtained, so an
    unlistable `Data/` does not turn every ordinary client into a shrug.
    """
    if not (client_dir / REPACK_FILE).is_file():
        return Check(REPACK_CHECK, "pass", f"no {REPACK_FILE} at the root of {client_dir}")
    if locales is None:
        return Check(
            REPACK_CHECK,
            "unchecked",
            f"{REPACK_FILE} sits at the root of {client_dir}, and the folders under {data} "
            "could not be listed to tell a repack from a full client — that is not a pass",
            "Check that the folder opens for you, then start the install again.",
        )
    if not locales:
        return Check(
            REPACK_CHECK,
            "warn",
            f"{REPACK_FILE} sits at the root of {client_dir} and there is no locale folder, "
            "which is how a repack looks",
            "Use a clean client of this expansion if extraction comes up short — repacks have "
            "rearranged archives and often extract incompletely.",
        )
    return Check(REPACK_CHECK, "pass", "nothing suggests a repack")


def _space_check(client_dir: Path, spec: ClientSpec, free_bytes: FreeBytes) -> Check:
    """Room on the CLIENT's own drive, which is not the one preflight measures.

    On Docker Desktop a shared folder is cached on the drive it lives on, and
    the scripts' extractors were seen to stall on a drive with no room for that
    cache. The client can sit on a different drive from the server folder, so
    preflight's own space checks say nothing about this one. Data carries the
    figure; nothing is measured here.

    `free is None` is "could not ask" and answers `unchecked`; zero is a real
    measurement of a full drive and answers `warn`. Folding the two together
    either invents "0 GB free" on a roomy machine or hides a genuinely full one.
    """
    free = free_bytes(client_dir)
    if free is None:
        return Check(
            SPACE_CHECK,
            "unchecked",
            f"the free space on the drive holding {client_dir} could not be measured — "
            "that is not a pass",
            f"Make sure that drive has at least {spec.near_client_warn_gb:.0f} GB free before "
            "extraction starts.",
        )
    gigabytes = free / GIB
    if gigabytes < spec.near_client_warn_gb:
        return Check(
            SPACE_CHECK,
            "warn",
            f"{gigabytes:.0f} GB free on the drive holding the client; "
            f"{spec.near_client_warn_gb:.0f} GB is the comfortable figure, because extraction "
            "reads the client through Docker's file sharing and that cache lands on this drive",
            "Free some space on that drive, or move the client to one that has room.",
        )
    return Check(SPACE_CHECK, "pass", f"{gigabytes:.0f} GB free on the drive holding the client")


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
