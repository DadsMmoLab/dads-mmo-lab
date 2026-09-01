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
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from yulon import docker, platform
from yulon.catalog.catalog import ExtractPlan, ExtractTool, RetrySpec
from yulon.catalog.installer import InstallerError
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


def counts(produces: Mapping[str, int], data_dir: Path) -> dict[str, int]:
    """`{dir: files present}` — one walk per output folder, taken once and shared.

    Split out of `shortfall()` so the gate and the sentence a user reads come
    from the SAME walk. Counted twice, a folder that stopped listing between the
    two calls makes the log line and the refusal disagree about the same number,
    and the number is the whole content of both.
    """
    return {folder: file_count(data_dir / folder) for folder in produces}


def short_of(seen: Mapping[str, int], produces: Mapping[str, int]) -> dict[str, tuple[int, int]]:
    """The threshold rule over counts already taken; no filesystem in it at all."""
    return {
        folder: (seen[folder], need) for folder, need in produces.items() if seen[folder] < need
    }


def shortfall(produces: Mapping[str, int], data_dir: Path) -> dict[str, tuple[int, int]]:
    """`{dir: (have, need)}` for every output folder under its threshold; empty means all met."""
    return short_of(counts(produces, data_dir), produces)


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


# ------------------------------------------------ running the plan, one container per tool

CLIENT_MOUNT = "/client"
"""Where the user's game client is mounted, read-only, in every extraction container."""

OUT_MOUNT = "/out"
"""Where `<server_dir>/data` is mounted read-write, and the container's working directory."""

EXTRACT_CANCEL_NOTE = "Finished tools are kept; only the tool that was interrupted runs again."
"""What a Stop costs in the extract stage — true because of the per-tool record."""

EXTRACT_HARDENING: tuple[str, ...] = ("--network", "none", "--security-opt", "no-new-privileges")
"""Two container options every extraction run gets, whatever the platform says.

The threat is not `git.py`'s ("the repository chooses the program"), but it is
not nothing either: these tools parse MPQ and WDT files, which are untrusted
binary input from a client somebody downloaded, and a memory-safety bug in a
C++ archive reader is the oldest bug there is. Both flags are free against a
tool that reads a folder and writes another one:

* `--network none` — nothing in an extraction plan resolves a name or opens a
  socket, the image has no entrypoint script, and a payload that got in through
  a malformed archive has nowhere to send anything. If a tool ever did want a
  network the failure is immediate and legible, not silent.
* `--security-opt no-new-privileges` — one of the two protections `container_t`
  was providing for free until `container_security_args()` turns it off with
  `label:disable` (`git.py` records the same pairing for the same reason). The
  tools exec nothing, least of all anything setuid.

**`--cap-drop ALL` and `--read-only` are deliberately NOT here.** Both change
what the process may do to the filesystem it must write — `--read-only` its own
root, `--cap-drop ALL` its ability to write a bind mount whose permissions do
not already allow it — and on Linux `platform.container_user_args()` has already
reduced the process to the invoking user, which is where most of `--cap-drop`'s
value would have come from. What is left is Docker Desktop, where the tool runs
as the image's root and where none of this can be measured from a test suite.
`docker.ContainerRun` records that they are unmeasured against these tools;
adding them on that footing would be shipping an install-blocking risk to buy
back very little, and `security_args` is where a measured set lands later.
"""


class RunContainer(Protocol):
    """`docker.run_container`'s shape, as a seam the tests fill with a recorder."""

    def __call__(
        self, spec: docker.ContainerRun, *, sink: docker.OutputSink, cancel: threading.Event | None
    ) -> docker.AttachedRun: ...


def container_security_args(*, enforcing: bool | None) -> tuple[str, ...]:
    """Every container-level security option one extraction run gets, from one answer.

    The hardening above, plus `platform.label_disable_args()`'s answer — asked
    ONCE per plan by `run_plan()` and handed to every tool, so three containers
    can never get three answers out of one `getenforce` that flickered.

    **The SELinux half is a correctness fix, not defence in depth.** Measured on
    `yulon-fedora-gate` (Fedora 44, Enforcing, Docker 29.7.2, 2026-09-01): a
    confined container is DENIED the user's game client outright. `data/` is
    fine without any label, because `stage_generate_compose` relabels the server
    directory and `data/` is created under it afterwards and inherits
    `container_file_t` — but the client is the user's own folder, somewhere else
    entirely, and no `chcon` of ours ever reaches it. `:z`/`:Z` are not the
    answer and must never be used on it: they RECURSIVELY relabel their source,
    which here is somebody's game install, and that is the same reason
    `docker.bind_mount_ok()`'s probe bans them. `label:disable` read the client
    and left its context byte-identical, and let `data/` be written as well.

    So this is one flag for the whole run rather than a suffix per mount: the
    container holds both the folder it must not touch and the folder it must
    write, and `platform.label_disable_args()` — `git.py`'s read path already
    asks it — keeps the three answers three. On "could not ask" nothing is
    added, because turning a container's confinement off is a security decision
    and taking one on no evidence is what that function exists to prevent.
    """
    return (*EXTRACT_HARDENING, *platform.label_disable_args(enforcing=enforcing))


STAGE_MOUNT = "/work"
"""Where the `stage_client` symlink farm is laid: the container's own writable layer.

Deliberately not a third bind and not a tmpfs. `--rm` takes the layer away with
the container, the farm is symlinks rather than bytes so it costs nothing to
build, and a third mount would be a third place to get the SELinux answer right
for — `container_security_args()`'s flag is container-wide precisely so that the
number of mounts stops mattering.
"""

STAGE_FAILED_RETURNCODE = 91
"""What `STAGE_SCRIPT` exits with when the farm could not be laid — before the tool ran.

Read together with `STAGE_FAILED_MARKER` and never alone: 91 is an ordinary
status a C++ extractor may exit with on its own, and reading it as "the farm
failed" would tell a user their tool never started when it had run for an hour.
`docker.cli_missing_run()` demands both halves of its sentinel for the same
reason and this follows it.
"""

STAGE_FAILED_MARKER = "yulon: the staged copy of the client could not be built"
"""The words `STAGE_SCRIPT` prints on that path; the other half of the signal."""

STAGE_SCRIPT = (
    "mkdir -p /work && cp -rs /client/. /work && cd /work || "
    '{ echo "yulon: the staged copy of the client could not be built"; exit 91; }; '
    '"$@"; status=$?; '
    "for name in $YULON_OUT_DIRS; do "
    '[ -e "$name" ] && mkdir -p "/out/$name" && cp -r "$name/." "/out/$name/"; '
    "done; exit $status"
)
"""The `stage_client` fallback: a symlink farm of the client inside the container.

For a tool that insists on cwd = client and writes beside `Data/`: `cp -rs` lays
symlinks to the read-only mount in a folder of the container's own writable
layer (gone with `--rm`), the tool runs there, and whatever it produced is
copied to `/out`. The client mount stays `:ro` — the point of the whole model —
and the argv's `/client` paths are rewritten to `/work` so the tool reads
through the farm. Data, not code: `stage_client: true`.

Three details are load-bearing and each of them is a wrong sentence avoided:

* **The farm's own failure is not the tool's.** `mkdir`, `cp -rs` and `cd` are
  one chain, and if any of them gives up the script says so in its own words and
  exits `STAGE_FAILED_RETURNCODE` WITHOUT running `"$@"`. `_conclude()` then
  says the tool never ran, rather than reporting a status the tool never
  produced.
* **The copy-back is by content into a folder we make.** This bullet used to say
  the plan's `cp -r "$name" /out/` nests as `/out/Buildings/Buildings` on a
  second pass. **It does not, and that reason is withdrawn.** Re-measured
  2026-09-01 on `debian:stable-slim` (GNU coreutils) and `alpine:3.20`
  (BusyBox), running the plan's script twice against one persistent `/out`:
  both merged flat into `/out/Buildings/`, identically. A relative source with
  an existing destination *directory* derives `dest/basename(src)`; only
  `cp -r "$name" "/out/$name"` nests, which both images also confirmed and
  which nothing here ever proposed. Anyone who meets `cp -r "$name" /out/` in
  an older draft should know it is not known-broken.

  The shipped spelling stays on two grounds that survive the re-measurement. It
  is idempotent across passes by construction rather than by a convention of
  `cp`'s about trailing components, so the invariant is visible in the line
  instead of in a manual page. And it is the only one that puts a `produces`
  name containing a slash where the count gate looks: measured the same day, a
  `produces` entry `Cameras/Buildings` lands at `/out/Buildings` under the
  plan's form and at `/out/Cameras/Buildings` under this one, while `counts()`
  reads `data_dir / folder` — so the plan's form would refuse that install for
  having produced nothing. No catalog entry names a slashed folder today and
  nothing in `ExtractTool` forbids one.
* **`$status` is the tool's.** Saved before the copy loop, so a `[ -e ]` that
  found nothing cannot turn a failed extraction into a success, and a successful
  extraction whose output folder is missing still reaches the count gate as
  "produced too little" rather than as a shell error.
"""


def _staged_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """The tool's own argv with `/client` paths pointed at the farm instead.

    Whole path components only: `/client` and anything under `/client/` move,
    while a longer name that merely starts with those letters (`/clientele`) is
    left alone — a prefix test without the separator would rewrite it into
    `/workele`.
    """
    return tuple(
        (
            STAGE_MOUNT + arg[len(CLIENT_MOUNT) :]
            if arg == CLIENT_MOUNT or arg.startswith(CLIENT_MOUNT + "/")
            else arg
        )
        for arg in argv
    )


def tool_run(
    plan: ExtractPlan,
    tool: ExtractTool,
    *,
    image_ref: str,
    client_dir: Path,
    data_dir: Path,
    user_args: Sequence[str],
    security_args: Sequence[str] = (),
) -> docker.ContainerRun:
    """The `docker run` for one tool, built by field so a test can assert it by field.

    `--ulimit stack=-1` is data (`ulimit_stack_unlimited`): the vanilla vmap
    extractor overflows the default stack on some maps and segfaults; nothing
    else needs it, so nothing else gets it. `stage_client` is data too: it swaps
    the argv for the `STAGE_SCRIPT` wrapper, the cwd for the farm, and names the
    tool's output folders in the environment so the script knows what to copy
    back. Neither changes a mount — the client is `:ro` in both modes.

    `user_args` and `security_args` both arrive already spelled, from
    `platform.container_user_args()` and `container_security_args()`. Neither is
    asked for here: this builds a description and asks the machine nothing, so a
    test gets the same spec on every host it runs on.

    Raises:
        InstallerError: the plan stages the client and one of this tool's output
            folders is named with whitespace. `for name in $YULON_OUT_DIRS`
            splits on it, so the folder would be looked for under two names that
            do not exist, nothing would be copied back, and the count gate would
            report the user's client as incomplete for a mistake in our catalog.
    """
    argv: tuple[str, ...] = tuple(tool.argv)
    workdir = OUT_MOUNT
    env: dict[str, str] = {}
    if plan.stage_client:
        loose = sorted(name for name in tool.produces if any(ch.isspace() for ch in name))
        if loose:
            raise InstallerError(
                f"{tool.name} stages the client, and its output folders {loose} are named with "
                "whitespace, which the staging script splits on — the catalog must name them "
                "without it."
            )
        argv = ("sh", "-c", STAGE_SCRIPT, "sh", *_staged_argv(tool.argv))
        workdir = STAGE_MOUNT
        env = {"YULON_OUT_DIRS": " ".join(tool.produces)}
    return docker.ContainerRun(
        image=image_ref,
        argv=argv,
        mounts=(
            docker.Mount(client_dir, CLIENT_MOUNT, read_only=True),
            docker.Mount(data_dir, OUT_MOUNT),
        ),
        workdir=workdir,
        env=env,
        user_args=tuple(user_args),
        security_args=tuple(security_args),
        ulimits=("stack=-1",) if plan.ulimit_stack_unlimited else (),
    )


def _retry_matches(
    retry: RetrySpec, run: docker.AttachedRun, cancel: threading.Event | None
) -> bool:
    """Did this ending look like the one the recipe names — and is another go the right answer?

    Five ways to answer "no", and every one of them is a decision rather than a
    fall-through:

    * **Exit 0.** Nothing failed; there is nothing to retry.
    * **The user pressed Stop** — `CANCELLED_RETURNCODE`, or a token set while
      the tool was exiting. A stopped tool's last lines can say anything,
      including the very words the recipe matches on, and starting a fresh
      container after somebody pressed Stop is the one thing the cancel path
      exists to prevent.
    * **Nothing was started at all** — no docker CLI. Running a program that is
      not there a second time is not a recipe for anything.
    * **The tail does not match.** The ordinary "this is a different failure".
    * **The pattern is not a usable regular expression.** A bug in our catalog,
      not in the user's machine: it is logged once, loudly, and answered "no
      retry", so what reaches the user is the tool's own failure — which is
      true — instead of a `re.error` out of a stage that is otherwise all
      yielded lines.
    """
    if run.returncode == 0 or run.returncode == docker.CANCELLED_RETURNCODE:
        return False
    if cancel is not None and cancel.is_set():
        return False
    if docker.cli_missing_run(run):
        return False
    try:
        return re.search(retry.when_log_matches, "\n".join(run.tail)) is not None
    except re.error as exc:
        logger.warning(
            f"the retry recipe's pattern {retry.when_log_matches!r} is not a usable regular "
            f"expression ({exc}), so nothing is re-run and the tool's own failure stands"
        )
        return False


def _retry_applies(
    retry: RetrySpec, tool: ExtractTool, run: docker.AttachedRun, cancel: threading.Event | None
) -> bool:
    """`_retry_matches`, plus the one thing a matching log is not enough for.

    The recipe must name the tool that just failed. It normally does — Vanilla's
    names `vmap extract`, which is the thing that segfaults, and `vmap assemble`
    with it because the assembler's input changed — but a recipe that named only
    OTHER tools would re-run them, skip past the crash with `continue`, and leave
    the tool that actually failed with no record and no refusal. Every later tool
    would then be satisfied, the stage would end with a success line, and the
    server would fail to load maps hours later. A recipe that cannot cover the
    failure does not apply to it, and the tool's own failure stands.
    """
    if not _retry_matches(retry, run, cancel):
        return False
    if tool.name not in retry.tools:
        logger.warning(
            f"{tool.name} failed the way the retry recipe describes, but the recipe re-runs "
            f"{list(retry.tools)}, which does not include it; retrying those would leave the "
            "tool that failed un-run and report an extraction nobody finished, so its failure "
            "stands instead"
        )
        return False
    return True


def _tool_named(plan: ExtractPlan, name: str) -> ExtractTool:
    """The plan's tool of that name, or the sentence that says the recipe is wrong.

    `ExtractPlan`'s own validator refuses a recipe naming a tool the plan does
    not have, so this cannot be reached from `catalog.json`; a plan built in code
    can still name a stranger, and one sentence beats a `StopIteration` from a
    generator expression halfway through an extraction.
    """
    for tool in plan.tools:
        if tool.name == name:
            return tool
    raise InstallerError(f"the retry recipe names {name!r}, which is not a tool of this plan")


def _stage_failed(run: docker.AttachedRun) -> bool:
    """Did `STAGE_SCRIPT` give up before the tool ran? Both halves, never one."""
    return run.returncode == STAGE_FAILED_RETURNCODE and any(
        STAGE_FAILED_MARKER in line for line in run.tail
    )


def run_plan(
    plan: ExtractPlan,
    *,
    image_ref: str,
    client_dir: Path,
    data_dir: Path,
    run_container: RunContainer,
    user_args: Sequence[str],
    sink: docker.OutputSink,
    cancel: threading.Event | None,
    required_file: str | None = None,
    client_build: int | None = None,
    selinux_enforcing: Callable[[], bool | None] | None = None,
) -> Iterator[str]:
    """Run every tool the evidence does not vouch for, recording each as it finishes.

    The evidence file is rewritten after every successful tool, not at the end,
    so a Stop between tools loses nothing — that is the cancel note's whole
    claim. Evidence written for another client or plan is replaced at the
    start: its records are worthless, and keeping them would let a count that
    happens to pass skip a tool that read the wrong archives.

    `required_file` is the client spec's; `client_build` is only spoken, in the
    shortfall refusal, because the count gate is the real check of the build.

    Two of the plan's fields are fallbacks, and both are said out loud rather
    than done quietly, because "it needed help" and "it went fine" are different
    facts about somebody's machine:

    * **`retry`** — the vanilla extractors crash on some maps and succeed on the
      next attempt. When a failure's log matches the recipe, the tools the recipe
      NAMES run again, once for the whole plan, and the log says which and why.
      A second matching crash is not retried again: it falls through to the
      refusal, which says it was already the retry. A crash that does not match
      is never retried and nothing in the log mentions one.
    * **`stage_client`** — `tool_run` wraps the tool in `STAGE_SCRIPT`. The
      wrapper is per-tool and invisible here, except that a farm that could not
      be built is reported as "the tool never ran" rather than as a status the
      tool never produced.

    `selinux_enforcing` is the seam for the one question here that is asked of
    the machine. It is resolved at call time rather than bound as a default, so
    a test that patches `platform.selinux_enforcing` is actually seen — the trap
    `platform.container_user_args()` records against itself — and it is asked
    exactly once, before the first container, for the reason
    `container_security_args()` gives.

    Raises:
        InstallerError: a tool failed, was cancelled, could not be started at
            all, or exited 0 with too few files.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    ask = selinux_enforcing if selinux_enforcing is not None else platform.selinux_enforcing
    security_args = container_security_args(enforcing=ask())
    expected = expected_evidence(plan, client_dir, required_file)
    current = read_evidence(data_dir)
    if current is not None and not same_stage(current, expected):
        yield "the extracted data is for another client or plan; extracting everything again"
        current = None
    if current is None:
        current = expected
        write_evidence(data_dir, current)

    def spec_for(which: ExtractTool) -> docker.ContainerRun:
        """One `tool_run` for both attempts: a retry is the same container, run again."""
        return tool_run(
            plan,
            which,
            image_ref=image_ref,
            client_dir=client_dir,
            data_dir=data_dir,
            user_args=user_args,
            security_args=security_args,
        )

    retried = False
    for tool in plan.tools:
        if tool_satisfied(tool, data_dir, current, expected):
            seen = counts(tool.produces, data_dir)
            yield f"{tool.name}: already extracted ({_counts_text(seen)})"
            continue
        yield f"{tool.name}: running {' '.join(tool.argv)}"
        run = run_container(spec_for(tool), sink=sink, cancel=cancel)
        if not retried and plan.retry is not None and _retry_applies(plan.retry, tool, run, cancel):
            # The vanilla extractors crash on some maps and succeed on the next
            # attempt; the recipe (data) names which tools to run again, once.
            #
            # "Once" has two guards and they are not the same guard.
            # `_retry_applies` is the reachable one: the recipe must cover the
            # tool that failed, so the pass always re-runs it and always
            # concludes it. `retried` is structural — with that rule every
            # recipe tool carries a fresh record when the pass ends, so no later
            # crash can reach here at all — and it stays because it is what
            # bounds this to one pass if the rule above is ever relaxed. A
            # second matching crash inside the pass never comes back here: it
            # goes straight to _conclude, which refuses it saying it was already
            # the retry.
            retried = True
            names = ", ".join(plan.retry.tools)
            yield (
                f"{tool.name} crashed the way the retry recipe expects; "
                f"running {names} again once"
            )
            for name in plan.retry.tools:
                again = _tool_named(plan, name)
                yield f"{again.name}: retrying {' '.join(again.argv)}"
                run = run_container(spec_for(again), sink=sink, cancel=cancel)
                current, seen = _conclude(
                    again,
                    run,
                    data_dir,
                    current,
                    cancel,
                    client_build,
                    staged=plan.stage_client,
                    retried=True,
                )
                yield f"{again.name}: done ({_counts_text(seen)})"
            continue
        current, seen = _conclude(
            tool, run, data_dir, current, cancel, client_build, staged=plan.stage_client
        )
        yield f"{tool.name}: done ({_counts_text(seen)})"


def _conclude(
    tool: ExtractTool,
    run: docker.AttachedRun,
    data_dir: Path,
    current: Evidence,
    cancel: threading.Event | None,
    client_build: int | None,
    *,
    staged: bool = False,
    retried: bool = False,
) -> tuple[Evidence, dict[str, int]]:
    """Turn one tool's exit into a record, or into the refusal that explains it.

    Five endings, kept five, because the sentence differs for each and four of
    them are not "the extraction failed":

    1. **Stopped.** `CANCELLED_RETURNCODE`, or a cancel token that was set while
       the tool was exiting (`run_attached()` only reports the sentinel while it
       is still reading, so a tool that finished in the same instant comes back
       0 — recording it and marching on would start the NEXT tool after the user
       said stop).
    2. **Never started.** No docker CLI to run at all. "The tool failed" is a
       sentence about a tool that ran; nothing did, and the help text is the
       whole answer. `docker.cli_missing_run()` owns that shape, both halves of
       it, because `docker run` returns the CONTAINER's status and a binary
       missing inside the image genuinely exits 127.
    3. **The staged farm could not be laid.** `stage_client` only: `STAGE_SCRIPT`
       gave up before `"$@"`, so the tool never started and the status is one no
       tool produced. Both halves of the signal are demanded, and only for a plan
       that actually stages — a tool is free to exit 91 on its own and to print
       whatever it likes while doing it.
    4. **Failed.** Its exit status and its last words, which is all we know —
       plus, when this attempt WAS the one retry the recipe asks for, that fact.
       "vmap extract failed (exit 139)" is equally true of the first crash and
       of the second, and a user who reads it after a second attempt they were
       not told about goes looking for a first one the message never mentions.
    5. **Finished and fell short.** Exit 0 with too few files: an incomplete
       client, the wrong expansion, a `data/` emptied by hand — or a folder
       nobody could list, which `file_count()` deliberately walks to "too few"
       so nothing is ever skipped on evidence nobody read. That is right, and it
       leaves the CAUSE unknown, so the refusal names both possibilities rather
       than accusing the user's client of a permissions problem in our own
       output folder.

    Only the fifth counts the folders, and it counts them once: the numbers in
    the refusal are the numbers the gate read, and so are the ones in the
    caller's "done" line.
    """
    if run.returncode == docker.CANCELLED_RETURNCODE or (cancel is not None and cancel.is_set()):
        raise InstallerError(f"{tool.name} was stopped. {EXTRACT_CANCEL_NOTE}")
    if docker.cli_missing_run(run):
        raise InstallerError(
            f"{tool.name} could not be started, so the client was never read. "
            f"{docker.last_words(run.tail)}"
        )
    if staged and _stage_failed(run):
        raise InstallerError(
            f"{tool.name} never ran: the staged copy of the client could not be built inside "
            f"the container, so no client was read. {docker.last_words(run.tail)}"
        )
    if run.returncode != 0:
        after = ", and that was already the one retry the plan's recipe asks for" if retried else ""
        raise InstallerError(
            f"{tool.name} failed (exit {run.returncode}){after}. Its last words were: "
            f"{docker.last_words(run.tail)}"
        )
    seen = counts(tool.produces, data_dir)
    short = short_of(seen, tool.produces)
    if short:
        told = ", ".join(
            f"{folder}: {have} files, at least {need} expected"
            for folder, (have, need) in short.items()
        )
        build = f" for client build {client_build}" if client_build is not None else ""
        raise InstallerError(
            f"{tool.name} finished but produced too little ({told}){build}. The server WILL "
            f"fail to load maps from this, so nothing was recorded. Check that the client "
            f"folder is a complete client of the right expansion, and that {data_dir} could "
            "be read — a folder that could not be listed counts as empty here, and the log "
            "names it — then try again."
        )
    record = ToolRecord(tool.name, argv_hash(tool.argv), int(time.time()))
    updated = with_record(current, record)
    write_evidence(data_dir, updated)
    return updated, seen


def _counts_text(seen: Mapping[str, int]) -> str:
    """Counts already taken, as one clause of a log line."""
    return ", ".join(f"{folder}: {have} files" for folder, have in seen.items())
