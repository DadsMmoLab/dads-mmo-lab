"""Client extraction: one `docker run --rm` per tool, and the evidence that lets a resume skip it.

The model is the Tortoise script's, adopted for every CMaNGOS game: the client
is mounted read-only at `/client`, `<server_dir>/data` read-write at `/out`,
the container's cwd is `/out`, and on Linux it runs as the invoking uid:gid
(`platform.container_user_args()`). Nothing is ever written into the user's
client and nothing is `chown`ed afterwards — the two things the TBC script's
`ExtractResources.sh a` model needed `sudo` for.

Evidence lives in `data/.yulon-extract.json` and a tool is skipped only when
three things agree: a completion record for THAT tool (name + argv hash,
written only after exit 0), every `produces` directory holding at least its
threshold of files, and the stage-level facts (plan hash, client path, the
required file's size and mtime) matching what this run would write. The
record is what makes the cancel note true: `ad` killed after 100 of ~700 dbc
would otherwise pass the count gate on resume and be skipped with a partial
set; the stage facts are what make a resume pointed at another client extract
everything again even though every count passes.

**Three answers, one bit.** "Has this tool already run?" is asked of a
filesystem, and a filesystem has a third answer to everything: an evidence file
that will not open, one that will not parse, a `stat()` on the client that
raises. `tool_satisfied` returns `bool`, so each of those has to be walked to a
side on purpose. They all go to **False — run it again**, because the two
mistakes are not the same size: a needless re-run costs an hour of somebody's
evening, while a wrong skip hands them a half-extracted client that looks
finished and fails later, in a server log, as missing maps. Nothing that was
merely *not disproved* is ever allowed to license a skip.

That principle is why `Evidence` carries `client_facts_complete` as well as the
facts. `required_file_size = None` means two different things — "this game names
no required file" (Tortoise: the client path is the only identity there is) and
"a required file was named and could not be measured" — and stored as two
`null`s they compare equal, so a run whose `stat()` failed would write a claim
that the NEXT failing run reads as a match. The flag keeps the states apart and
`satisfied()` refuses the skip on either side of the comparison carrying it.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from yulon.catalog.catalog import ExtractPlan, ExtractTool
from yulon.log import get_logger

logger = get_logger(__name__)

EVIDENCE_FILE = ".yulon-extract.json"
"""Under `data/`, beside the folders it vouches for, so deleting `data/` deletes the claim."""

HASH_LENGTH = 16


@dataclass(frozen=True)
class ToolRecord:
    """One tool finished with exit 0, running exactly this argv, at this time."""

    name: str
    argv_hash: str
    finished_unix: int


@dataclass(frozen=True)
class Evidence:
    """What `data/` was extracted from, and which tools finished doing it."""

    plan_hash: str
    client_path: str
    required_file_size: int | None
    required_file_mtime: int | None
    tools: tuple[ToolRecord, ...]
    client_facts_complete: bool = True
    """Were `client_path` and the required file's facts actually obtained?

    False says "this record cannot identify the client it came from" — the
    `stat()` raised, or the path would not resolve — and `satisfied()` refuses
    every skip on either side of a comparison carrying it. Defaulted True so
    that constructing an `Evidence` from facts you have in hand needs no
    ceremony; only `expected_evidence()`, which is where the measuring happens,
    ever sets it False.
    """

    def record_for(self, name: str) -> ToolRecord | None:
        return next((record for record in self.tools if record.name == name), None)


def _short_digest(canonical: str) -> str:
    """The one digest in this module: sha256 of a canonical string, first 16 hex.

    Both hashes go through it so they cannot drift into two lengths, and so
    "what was hashed" is the only thing either caller has to think about.
    """
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def argv_hash(argv: Sequence[str]) -> str:
    """A short, stable digest of one tool's argv — the record's identity beside its name.

    The list is serialised, not joined: `("a b",)` and `("a", "b")` are two
    different command lines and a joined hash cannot tell them apart, so an
    edited catalog block that merely re-split an argument would keep its old
    record and be skipped.
    """
    return _short_digest(json.dumps(list(argv), separators=(",", ":")))


def plan_hash(plan: ExtractPlan) -> str:
    """Digest of the whole plan, so an edited catalog block re-extracts rather than skips."""
    return _short_digest(
        json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )


def expected_evidence(plan: ExtractPlan, client_dir: Path, required_file: str | None) -> Evidence:
    """The stage-level facts THIS run would write, with no tool records yet.

    The required file's size and mtime are the cheapest proof that the client
    is the one that was extracted from; a client that was patched or swapped
    changes both. `None` when the spec names no required file (Tortoise), in
    which case the client path is the only identity there is.

    Two filesystem calls happen here and either can fail. Neither is allowed to
    escape as a raw `OSError` out of a stage that is otherwise all `yield`ed
    lines, and neither is allowed to quietly produce the same two `null`s that
    "this game names no required file" produces. Both set
    `client_facts_complete=False` instead, which says "I could not identify the
    client" in a way that survives being written to disk and read back — and
    which no comparison, not even with itself, turns into a skip.
    """
    complete = True
    try:
        client_path = str(client_dir.resolve())
    except OSError as exc:
        logger.warning(
            f"could not resolve the client folder {client_dir}: {exc}; this run cannot say which "
            "client it extracted from, so nothing will be skipped on a resume"
        )
        client_path = str(client_dir)
        complete = False
    size: int | None = None
    mtime: int | None = None
    if required_file is not None:
        try:
            stat = client_dir.joinpath(*required_file.split("/")).stat()
        except OSError as exc:
            logger.warning(
                f"could not read the size and date of {required_file} under {client_dir}: {exc}; "
                "this run cannot say which client it extracted from, so nothing will be skipped "
                "on a resume"
            )
            complete = False
        else:
            size, mtime = stat.st_size, int(stat.st_mtime)
    return Evidence(plan_hash(plan), client_path, size, mtime, (), client_facts_complete=complete)


def read_evidence(data_dir: Path) -> Evidence | None:
    """The evidence file, or None when absent, unreadable or malformed — never a guess.

    Opened ONCE. `is_file()` followed by `read_text()` is two reads of one file
    and therefore two answers that can disagree; the I.3 incident is what that
    disagreement looks like when it reaches a user (a transient failure on the
    second read reported as "Yu'lon did not write this file"). Here the two
    answers would be worse: the file that decides whether an hour of extraction
    is repeated.

    Absent and unreadable both answer None, deliberately and for the same
    reason — a skip may only be licensed by evidence somebody actually read —
    but they are logged differently, because "there is no evidence yet" is the
    ordinary first run and "there is evidence I cannot open" is a problem with
    the folder.
    """
    path = data_dir / EVIDENCE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning(
            f"{path} could not be read ({exc}), so what has already been extracted is unknown "
            "— that is not a skip; the extraction tools will run again"
        )
        return None
    try:
        return _parse(text)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning(f"{path} is not usable extraction evidence, ignoring it: {exc}")
        return None


def _parse(text: str) -> Evidence:
    """One JSON document into an `Evidence`, or an exception `read_evidence` turns into None.

    Structural checks rather than duck typing, so a hand-edited or truncated
    file becomes "not usable evidence" with a sentence naming what was wrong,
    and never a half-built `Evidence` whose missing half compares equal to
    something.
    """
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise TypeError(f"the file holds a JSON {type(raw).__name__}, not an object")
    complete = raw["client_facts_complete"]
    if not isinstance(complete, bool):
        raise TypeError(f"client_facts_complete is {complete!r}, which is not true or false")
    tools = raw["tools"]
    if not isinstance(tools, list):
        raise TypeError(f"tools is a {type(tools).__name__}, not a list")
    return Evidence(
        plan_hash=str(raw["plan_hash"]),
        client_path=str(raw["client_path"]),
        required_file_size=_optional_int(raw["required_file_size"]),
        required_file_mtime=_optional_int(raw["required_file_mtime"]),
        tools=tuple(
            ToolRecord(str(record["name"]), str(record["argv_hash"]), int(record["finished_unix"]))
            for record in tools
        ),
        client_facts_complete=complete,
    )


def _optional_int(value: Any) -> int | None:
    """`None` stays `None`; anything else must be a number or the file is not usable."""
    return None if value is None else int(value)


def write_evidence(data_dir: Path, evidence: Evidence) -> None:
    """Atomic, so an interrupted write cannot leave half a claim that reads as a whole one."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / EVIDENCE_FILE
    tmp = path.with_name(path.name + ".yulon-new")
    try:
        tmp.write_text(
            json.dumps(asdict(evidence), indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        os.replace(tmp, path)  # atomic on POSIX and on Windows
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def with_record(evidence: Evidence, record: ToolRecord) -> Evidence:
    """`evidence` with `record` added, replacing an older record of the same tool."""
    kept = tuple(existing for existing in evidence.tools if existing.name != record.name)
    return replace(evidence, tools=(*kept, record))


def same_stage(evidence: Evidence, expected: Evidence) -> bool:
    """Do two evidences describe one extraction? Tool records are not part of that.

    Four facts, and each of them alone decides the answer. `client_facts_complete`
    is deliberately NOT one of them: it is not a fact about the extraction but a
    statement about how much we know, and comparing it here would look like a
    fifth guard while catching nothing `satisfied()`'s veto does not already
    catch — the veto has to be there anyway, because `run_mmaps` compares an
    evidence with ITSELF, where every field agrees by construction. One rule,
    one place.
    """
    return (
        evidence.plan_hash == expected.plan_hash
        and evidence.client_path == expected.client_path
        and evidence.required_file_size == expected.required_file_size
        and evidence.required_file_mtime == expected.required_file_mtime
    )


def file_count(folder: Path) -> int:
    """Regular files under `folder`, at any depth; 0 for a folder that is not there.

    Walked with `iterdir()` rather than `Path.rglob()`, which is the choice I.1
    made in `clientdir.mpq_files()` and for the same incident: rglob answers an
    unreadable folder with a short list and no sound, and a short list here is
    a sentence — "the tool produced too few files" — about a folder nobody
    could open. The count is what was actually seen, every refusal to list is
    logged, and the number is never rounded UP: an unreadable output folder
    comes out short, which re-runs the tool rather than skipping it.

    Directory symlinks are not followed (matching `rglob`), which also means a
    loop cannot hang the count; a symlink to a file still counts as a file.
    """
    total = 0
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            entries = list(current.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue  # nothing extracted there yet, and 0 is the true answer
        except OSError as exc:
            logger.warning(
                f"could not list {current} while counting extraction output: {exc}; it counts as "
                "what was seen, which is short — the tool will run again rather than be skipped"
            )
            continue
        for entry in entries:
            if entry.is_dir() and not entry.is_symlink():
                pending.append(entry)
            elif entry.is_file():
                total += 1
    return total


def shortfall(produces: Mapping[str, int], data_dir: Path) -> dict[str, tuple[int, int]]:
    """`{dir: (have, need)}` for every output folder under its threshold; empty means all met."""
    short: dict[str, tuple[int, int]] = {}
    for folder, need in produces.items():
        have = file_count(data_dir / folder)
        if have < need:
            short[folder] = (have, need)
    return short


def tool_satisfied(
    tool: ExtractTool, data_dir: Path, evidence: Evidence | None, expected: Evidence
) -> bool:
    """The three-part rule: stage facts match, a record for this argv exists, the counts pass.

    True means "this tool already did its work; a resume may skip it". False
    means "run it" — and it is the answer to every question this module cannot
    settle, so the caller never has to wonder which kind of False it got.
    """
    return satisfied(tool.name, tool.argv, tool.produces, data_dir, evidence, expected)


def satisfied(
    name: str,
    argv: Sequence[str],
    produces: Mapping[str, int],
    data_dir: Path,
    evidence: Evidence | None,
    expected: Evidence,
) -> bool:
    """`tool_satisfied` by parts, so the mmaps stage can ask without an `ExtractTool`.

    In order, and every one of them a real incident:

    1. **The stage facts.** No evidence at all, facts nobody could obtain, or
       facts that describe another extraction: a resume pointed at a different
       client passes every count and every record, because the counts are about
       `data/` and the records are about tools — neither of them looks at the
       client. This is the part that does.
    2. **The record for THIS tool.** Written only after exit 0, and only for
       the argv that produced it. `ad` killed after 100 of ~700 dbc files has
       already passed its threshold of 3, so without the record the count gate
       would skip it and the install would finish with a partial set. An edited
       argv is the same shape: the folder is full of output from a command we
       no longer run.
    3. **The counts.** The record says a tool exited 0; the folders say what is
       actually there. A `data/` that was cleaned out by hand, or a tool that
       exited 0 having written nothing, is caught here and nowhere else.

    Anything unknown answers False. `client_facts_complete` is checked on BOTH
    sides because `run_mmaps` compares an evidence with itself: "I do not know
    which client this was" cannot be made true by asking it twice.
    """
    if evidence is None:
        return False
    if not (evidence.client_facts_complete and expected.client_facts_complete):
        logger.info(
            f"{name}: the client this data came from could not be identified, so nothing is "
            "skipped — it runs again"
        )
        return False
    if not same_stage(evidence, expected):
        return False
    record = evidence.record_for(name)
    if record is None or record.argv_hash != argv_hash(argv):
        return False
    return not shortfall(produces, data_dir)
