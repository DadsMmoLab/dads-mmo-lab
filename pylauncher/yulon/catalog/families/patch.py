"""Tolerant application of a unified diff to a cloned checkout: the `patch-sources` stage kind.

Why this exists at all is `pyplan/upstream-cmangos-doodad-drop.md`: a defect in
CMaNGOS's `vmap_extractor` drops 14.8% of a Vanilla world's placed collision
geometry on every case-sensitive filesystem, silently, and §10 of that page
recommended carrying the fix as a patch applied after `clone-sources`, against
pinned revisions, until upstream takes it. The CMaNGOS family binds this module
at `patch-sources`; the patch file itself ships as data beside the family's
compose templates and the catalog names it per entry.

**Why not `git apply`.** The clone seam is containerised `alpine/git` on Linux
and the host's git on Windows, and the state of a checkout after a failed apply
differs between the two (`git apply` is atomic per invocation; `patch(1)` is
not, and is not on every box). Applying in Python makes the outcome the same
on every platform, makes "already applied" a first-class answer rather than a
`Reversed (or previously applied) patch detected!` prompt, and keeps the
refusal sentence in this module's hands. The patch file stays a real unified
diff so the same bytes are what the upstream report attaches.

**Tolerant, in exactly two ways, and strict in every other.** A hunk whose
pre-image is found (once) is applied, at the stated line or at an offset from
it — an unrelated line added above the function moves the context without
changing it, and refusing every install over that would be the wrong trade. A
hunk whose pre-image is absent but whose post-image is present (once) is
already applied and is left alone — that is what makes the stage survive the
day upstream lands their own fix, and what makes a resume safe whether or not
the state file says the stage ran. Anything else refuses, naming the file and
the line the patch expected, and NOTHING is written: every hunk of every file
is resolved before the first byte goes to disk, so a patch that half-applies
cannot leave a checkout that half-compiles.

**Which question is asked first, and why it is not always the same one.** For a
hunk that removes at least one line, the pre-image is the test: applying it
destroys the pre-image, so finding it means the work is still to do. For a hunk
that removes NOTHING the pre-image is pure context and applying it leaves that
context intact, so the post-image has to be asked about first or an insertion at
the head or the tail of its own block applies again on every press. Measured
2026-09-05 before the order was fixed: three presses of a `+int d;` hunk left
three copies of `int d;`. Every hunk of the patch this module ships removes
nothing, so this is the ordinary path here rather than a corner of it.

What "found once" costs: a pre-image that matches in two places is ambiguous
and refuses, even though `patch(1)` would take the first. The hunks this ships
carry six lines of context and there is no second `fixedName =
GetPlainName(origPath.c_str());` in that tree, so the strictness is free
today, and the day it is not is a day someone should be looking at the file.

Line endings are the file's own: a checkout is compared and rewritten with
the terminator it already uses, and the patch's lines are matched without
theirs. `git.py` clones with `core.autocrlf=false`, so in practice the files
are LF on every platform, but a CRLF file is a case the tests drive rather
than an assumption the module makes.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(RuntimeError):
    """A patch that cannot be read or applied as it stands; the message is the sentence."""


@dataclass(frozen=True)
class Hunk:
    """One `@@` block: the lines it expects to find and the lines it leaves behind."""

    path: str
    """The file, POSIX-relative to the checkout root (the `+++ b/` path with its prefix off)."""
    start: int
    """The 1-based line the pre-image was at when the patch was made — a hint, not a rule."""
    before: tuple[str, ...]
    """Context and `-` lines, in order: what must be on disk for this hunk to apply."""
    after: tuple[str, ...]
    """Context and `+` lines, in order: what is on disk once it has."""
    removals: int
    """How many `-` lines this hunk had. Zero means its pre-image is pure context.

    Not derivable from `before` and `after` afterwards, and the whole of
    `apply()`'s ordering rule turns on it -- see the `removals == 0` branch
    there. A hunk that only inserts leaves its own pre-image intact.
    """


class Outcome(enum.Enum):
    APPLIED = "applied"
    PRESENT = "present"


@dataclass(frozen=True)
class FileResult:
    """What happened to one file: how many hunks were written, how many were already there."""

    path: str
    applied: int
    present: int

    @property
    def outcome(self) -> Outcome:
        return Outcome.APPLIED if self.applied else Outcome.PRESENT


def parse(text: str) -> tuple[Hunk, ...]:
    """Read a unified diff into hunks, refusing anything this module does not apply.

    Accepted: `diff --git`/`index` headers, `--- a/…`/`+++ b/…` pairs, `@@` hunks
    of ` `, `-`, `+` lines and `\\ No newline at end of file`. Refused by name:
    file creation and deletion (`/dev/null`), binary patches, renames, and a hunk
    whose line counts do not add up — a patch file that was edited by hand.
    """
    hunks: list[Hunk] = []
    lines = text.splitlines()
    path: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old = line[4:].split("\t")[0]
            if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
                raise PatchError(f"patch line {i + 1}: `---` is not followed by `+++`")
            new = lines[i + 1][4:].split("\t")[0]
            if old == "/dev/null" or new == "/dev/null":
                raise PatchError(
                    f"patch line {i + 1}: creates or deletes {new if old == '/dev/null' else old}; "
                    "this stage only edits files a checkout already has"
                )
            if _strip_prefix(old) != _strip_prefix(new):
                raise PatchError(
                    f"patch line {i + 1}: renames {_strip_prefix(old)} to {_strip_prefix(new)}; "
                    "this stage does not rename"
                )
            path = _strip_prefix(new)
            i += 2
            continue
        if line.startswith("@@"):
            header = _HUNK_HEADER.match(line)
            if header is None or path is None:
                raise PatchError(f"patch line {i + 1}: hunk header {line!r} has no file above it")
            old_start = int(header.group(1))
            old_count = int(header.group(2) or 1)
            new_count = int(header.group(4) or 1)
            before: list[str] = []
            after: list[str] = []
            removals = 0
            i += 1
            while i < len(lines) and (len(before) < old_count or len(after) < new_count):
                body = lines[i]
                if body.startswith("\\"):
                    i += 1
                    continue
                if body.startswith(" ") or body == "":
                    before.append(body[1:])
                    after.append(body[1:])
                elif body.startswith("-"):
                    before.append(body[1:])
                    removals += 1
                elif body.startswith("+"):
                    after.append(body[1:])
                else:
                    raise PatchError(f"patch line {i + 1}: {body!r} is not a hunk line")
                i += 1
            if len(before) != old_count or len(after) != new_count:
                raise PatchError(
                    f"patch line {i}: the hunk at {path}:{old_start} says {old_count}/{new_count} "
                    f"lines and carries {len(before)}/{len(after)}"
                )
            hunks.append(Hunk(path, old_start, tuple(before), tuple(after), removals))
            continue
        if line.startswith("Binary files") or line.startswith("GIT binary patch"):
            raise PatchError(f"patch line {i + 1}: binary patches are not applied")
        if line.startswith(("rename ", "similarity index", "new file mode", "deleted file mode")):
            raise PatchError(
                f"patch line {i + 1}: {line!r} — this stage edits files in place and does "
                "nothing else"
            )
        if line and not line.startswith(("diff --git ", "index ", "\\")):
            # Strict on purpose: a line the parser cannot place is a hunk whose
            # header under-counted it, or a patch file edited by hand, and both
            # would otherwise apply as something other than what was written.
            raise PatchError(f"patch line {i + 1}: {line!r} is not a line this module reads")
        i += 1
    if not hunks:
        raise PatchError("the patch has no hunks")
    return tuple(hunks)


def _strip_prefix(path: str) -> str:
    """`a/x/y.cpp` -> `x/y.cpp`; a path with no such prefix is taken as it is."""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def apply(text: str, root: Path, *, name: str, dry_run: bool = False) -> tuple[FileResult, ...]:
    """Apply `text` under `root`; per file, how many hunks were written and how many were there.

    `name` is what the refusal calls the patch. Resolution happens for every
    hunk of every file BEFORE any file is written, and a refusal writes nothing.

    `dry_run` resolves everything and writes nothing, so the answer is the same
    `FileResult` tuple the real call would return: `applied` is then "would be
    written". It exists because a caller can need the answer BEFORE deciding
    whether patching is the right thing to do at all -- `CmangosInstaller.
    _patch_sources()` refuses a checkout whose build this press is going to
    skip, and "would this patch change anything?" is the question that refusal
    turns on. It is a second full resolution rather than a cached one on
    purpose: two reads of four small files cost nothing, and a plan carried
    between calls is a plan that can go stale against the disk.
    """
    hunks = parse(text)
    by_file: dict[str, list[Hunk]] = {}
    for hunk in hunks:
        by_file.setdefault(hunk.path, []).append(hunk)
    planned: list[tuple[Path, bytes, FileResult]] = []
    for rel, file_hunks in by_file.items():
        target = _inside(root, rel, name)
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            raise PatchError(
                f"{name} patches {rel}, and this checkout has no such file. The source tree at "
                f"{root} is not the one the patch was written against; nothing was changed."
            ) from None
        except OSError as exc:
            raise PatchError(f"{name} could not read {target}: {exc}") from exc
        eol = b"\r\n" if b"\r\n" in raw else b"\n"
        body = raw.decode("utf-8", errors="surrogateescape")
        lines = body.split("\n")
        trailing = lines[-1] == ""
        if trailing:
            lines.pop()
        lines = [ln[:-1] if ln.endswith("\r") else ln for ln in lines]
        applied = present = 0
        for hunk in file_hunks:
            if hunk.removals == 0 and _find(lines, hunk.after, hint=hunk.start - 1) is not None:
                # ORDER, and it is the whole of this branch. A hunk with no `-`
                # lines has a pre-image made only of context, and applying it
                # does not disturb that context -- so when the insertion sits at
                # the HEAD or the TAIL of the block, the pre-image is still
                # contiguous afterwards. Asking "is the pre-image here?" first
                # then answers yes on a file that already carries the fix, the
                # post-image check below never runs, and the hunk applies again.
                #
                # Driven on 2026-09-05 with this module: pre-image
                # `int a;/int b;/int c;` and a `+int d;` at the end gave
                # `int a;int b;int c;int d;` on press 1, a second `int d;` on
                # press 2 and a third on press 3. `patch-sources` reads the
                # files on EVERY press by design (its own docstring says the
                # record must not be what skips it), so this is that stage's
                # ordinary path, not an edge of it.
                #
                # The other repair `parse()` could have made -- refuse this
                # shape outright -- was not available: all five hunks of the
                # patch this module ships are insertions and none of them
                # removes a line, so refusing the shape refuses the cargo. They
                # survive today only because each `+` block happens to land in
                # the MIDDLE of its context, which is a fact about where
                # upstream put its blank lines and not a property anyone chose.
                #
                # A post-image found more than once (`_find` -> -1) counts as
                # present here, exactly as it does in the branch below: if the
                # lines this hunk would write are already somewhere in the file,
                # writing another copy is the one outcome that is certainly
                # wrong.
                present += 1
                continue
            where = _find(lines, hunk.before, hint=hunk.start - 1)
            if where is None:
                if _find(lines, hunk.after, hint=hunk.start - 1) is not None:
                    present += 1
                    continue
                raise PatchError(
                    f"{name} does not apply: {rel} no longer has the lines it expects at line "
                    f"{hunk.start} (`{_missing_line(lines, hunk.before)}`). Upstream has moved "
                    "under this patch, so it was not applied and nothing was changed."
                )
            if where == -1:
                raise PatchError(
                    f"{name} does not apply: the lines it expects at {rel}:{hunk.start} occur "
                    "more than once in that file, so which to change is ambiguous; nothing "
                    "was changed."
                )
            lines[where : where + len(hunk.before)] = list(hunk.after)
            applied += 1
        result = FileResult(rel, applied, present)
        if applied:
            out = eol.join(ln.encode("utf-8", errors="surrogateescape") for ln in lines)
            if trailing:
                out += eol
            planned.append((target, out, result))
        else:
            planned.append((target, b"", result))
    results: list[FileResult] = []
    for target, out, result in planned:
        if result.applied and not dry_run:
            try:
                target.write_bytes(out)
            except OSError as exc:
                raise PatchError(f"{name} could not write {target}: {exc}") from exc
        results.append(result)
    return tuple(results)


def _inside(root: Path, rel: str, name: str) -> Path:
    """`root / rel`, refusing a patch path that would leave the checkout."""
    posix = PurePosixPath(rel)
    if posix.is_absolute() or ".." in posix.parts or "\\" in rel:
        raise PatchError(f"{name} names {rel!r}, which is not a path inside the checkout")
    return root.joinpath(*posix.parts)


def _find(lines: list[str], block: tuple[str, ...], *, hint: int) -> int | None:
    """Where `block` occurs in `lines`: the index, -1 for more than one, None for none.

    The hinted position is tried first and wins outright when it matches, so a
    file whose own text happens to repeat the block elsewhere still applies at
    the line the patch named; the whole-file search is for the offset case.
    """
    n = len(block)
    if not n:
        return None
    if 0 <= hint <= len(lines) - n and lines[hint : hint + n] == list(block):
        return hint
    hits = [i for i in range(len(lines) - n + 1) if lines[i : i + n] == list(block)]
    if not hits:
        return None
    if len(hits) > 1:
        return -1
    return hits[0]


def _missing_line(lines: list[str], block: tuple[str, ...]) -> str:
    """The first expected line the file has nowhere — the one that moved — else the first."""
    present = set(lines)
    for line in block:
        if line.strip() and line not in present:
            return line.strip()
    for line in block:
        if line.strip():
            return line.strip()
    return ""
