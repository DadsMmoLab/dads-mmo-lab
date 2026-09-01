r"""The conf stage kind: patch `Key = value` lines in an emulator's ini-style conf files.

One job, family-neutral: a `ConfPatchTable` from `catalog.json` says which files, which
keys and which values (with `{{TOKEN}}`s), and this module turns that into text. The bash
installers did the same work with `sed`, one expression per key, and the point of
replacing them is to stop doing it approximately.

**A key has three states, not two**, and each gets its own answer here:

* **present and set** (`^Key\s*=`) — REWRITTEN in place, so the file keeps its order and
  its comments. The scripts overwrote every conf on every run, which threw away a user's
  other edits with each reinstall. EVERY active spelling is rewritten, not the first: the
  emulator reads the file top to bottom and the last assignment wins, so a patch that
  stopped at the first would leave the original value in force further down.
* **present but commented** (`^#\s*Key\s*=`) — uncommented in place, and only when the
  patch says `match_commented`. The real `playerbot/aiplayerbot.conf.dist.in` ships
  `AiPlayerbot.SyncLevel*` as `# Key = 0` and nothing else, which is what the Vanilla
  installer's seds were uncommenting; everywhere else a commented key is documentation
  and is left alone. Only the FIRST commented spelling is uncommented — a second is
  usually the upstream default, and live it would win and undo the patch.
* **absent** — appended at the end, because a conf the emulator reads with the key
  missing silently takes a default, which is how a 500-bot install boots with 50.

An active line always beats a commented twin: a file carrying both `# Foo = 1` and
`Foo = 2` is one nobody can reason about, so the comment stays a comment.

There is no fourth state here for a file that could not be READ. `patch()` takes text and
returns text — it never opens anything — so it cannot tell an empty file from an
unreadable one and does not pretend to; empty text is deliberately a conf with no keys in
it, and all of them are appended. The read/unreadable distinction belongs to `_read()`,
below `materialise()` on the side of the seam that does the opening, and it is a
`InstallerError` naming the path — for a file that is missing, is a directory, or is not
UTF-8. That last one needs saying out loud: a `UnicodeDecodeError` is a `ValueError`, not
an `OSError`, so the obvious `except OSError` lets it escape a resume as a raw traceback
about a byte offset in a file it does not name. `_write()` answers to that rule from the
other side and for the same reason — a `UnicodeEncodeError` is a `ValueError` too — so the
two are one decision rather than two.

**Where a conf comes from, and what is never done to it again.** `materialise()` copies
the image's `*.conf.dist` out ONCE per file and strips the suffix; `apply_table()` patches
what is there, in place, and writes only when the bytes change. A file that exists is
never re-copied, because by then it may be one the user has edited — which is the whole
reason this module patches rather than doing what the bash installers did, which was to
overwrite every conf on every run.

**Line endings are the subject, not a detail.** The two real files a single CMaNGOS
install patches disagree: `mangos-classic`'s `mangosd.conf.dist.in` and
`realmd.conf.dist.in` are LF, and `playerbots`' `aiplayerbot.conf.dist.in` is CRLF
throughout. So a hard-coded newline is wrong for one of them, and half-wrong is a file
with mixed endings — a real defect, and one this project has already come within a commit
of shipping twice. Every existing line keeps the ending it arrived with, and a line this
module INVENTS takes the ending of the file's first line.

Tokens go through `composegen.fill`, the one `{{TOKEN}}` grammar in the app (contract A6),
so an unknown token is an error rather than a literal `{{DB_HOST}}` in a conf file that
the emulator would then try to connect to.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from yulon.catalog.catalog import ConfPatch, ConfPatchTable
from yulon.catalog.composegen import ComposeGenError, fill
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

CONF_MODE = 0o600
"""Every conf this module writes is owner-only: the database password is in it.

**Kept at `0o600` deliberately, and it is only correct while the images run as root.**
These files are written by the HOST user and read by the server INSIDE the container, so
the mode here and the `USER` in the Dockerfile are one question answered in two places.
Today the answer lines up: `wow-*/native/Dockerfile.tmpl` declares no `USER` and
`shared/cmangos/*.yml.tmpl` carries no `user:` key, so `mangosd` is root and root bypasses
POSIX permission checks — a host-owned `0600` conf is readable anyway.

Widening it was considered and refused: the file holds the database password in clear text
(`LoginDatabaseInfo = "host;port;user;password;schema"`), which is exactly what a mode is
for, and widening it now to protect against a change nobody has made would be paying the
real cost for the hypothetical one.

What the trap deserved instead is being loud. A future hardening pass adding a non-root
`USER` is an ordinary, well-intentioned change; it would make every conf unreadable to the
server, and the symptom is "it will not boot" with nothing pointing at a constant three
modules away. `test_the_conf_mode_stays_owner_only_only_while_the_images_run_as_root`
reads the shipped templates and goes red the moment one of them stops running as root, in
the same commit that does it, naming this constant. That is the "one place" the two facts
answer to, short of moving the mode into the catalog beside the Dockerfile — which is a
schema change and the owner's call, not J.2's.
"""

DIST_SUFFIX = ".dist"
"""What the images append to a template conf; stripped when the file is materialised."""

_STAGING_DIR = ".yulon-conf-dist"
"""Where the image's conf directory lands between the `docker cp` and the move.

Inside `etc_dir`, so the move off it is a rename on one filesystem rather than a copy —
`etc_dir` lives under the user's server directory, which on Windows is routinely a
different volume from `%TEMP%`. Named with a leading dot and the app's own name so an
interrupted run leaves something a person can recognise and delete.
"""

_TEMP_SUFFIX = ".yulon-new"
"""Suffix of the file `_write` renames over a conf, so a conf is never half-written."""


class CopyFromImage(Protocol):
    """`docker.copy_from_image`'s shape: `docker create` + `cp` + `rm`, no shell.

    Injected rather than imported, so this module stays testable without a daemon and the
    families layer keeps one seam to Docker. Its errors are NOT caught here: it already
    keeps three outcomes apart — no CLI at all, a source the image does not ship, and
    everything else with docker's stderr kept whole — and `DockerCliMissingError`
    subclasses `DockerCommandError`, so any `except` broad enough to be convenient here
    would swallow the one message a user can act on.
    """

    def __call__(self, image: str, src: str, dest: Path) -> None: ...


def patch(text: str, patch: ConfPatch, tokens: Mapping[str, str]) -> str:
    r"""`text` with every key in `patch.keys` set, byte-preserving everywhere else.

    Pure, so a test can assert the exact bytes. Keys are matched at column 0, which is
    where every shipped conf writes them; an indented spelling is left alone and the key
    is appended below it, because a regex loose enough to catch the indented case also
    fires on the `#    SyncLevel` prose lines these files are full of.

    Raises:
        InstallerError: a value carried an unknown `{{TOKEN}}`, or filled out to
            something with a line break in it.
    """
    lines = text.splitlines(keepends=True)
    for key, raw in patch.keys.items():
        replacement = _line(key, raw, tokens)
        active = re.compile(rf"^{re.escape(key)}\s*=")
        hit = False
        for index, line in enumerate(lines):
            body, ending = _split_ending(line)
            if active.match(body):
                lines[index] = replacement + ending
                hit = True
        if hit:
            continue
        if patch.match_commented:
            commented = re.compile(rf"^#\s*{re.escape(key)}\s*=")
            for index, line in enumerate(lines):
                body, ending = _split_ending(line)
                if commented.match(body):
                    lines[index] = replacement + ending
                    hit = True
                    break
        if hit:
            continue
        newline = _newline_of(lines)
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(replacement + newline)
    return "".join(lines)


def materialise(
    table: ConfPatchTable, *, image_ref: str, etc_dir: Path, copy_from_image: CopyFromImage
) -> tuple[Path, ...]:
    """Copy every table file that is missing from `etc_dir` out of the image, `.dist` stripped.

    ONE `docker cp` of the whole `source_dir` for however many files are missing — a
    create/cp/rm round trip per file would be three docker calls times the table — and NO
    round trip at all when nothing is missing, which is the resume case and the common
    one. That check comes first for a reason beyond speed: a resumed install may be
    running against an image that is no longer on the machine.

    A file that already exists is never touched. By the second run it may be one the user
    has edited, and re-copying would throw that away; patching it in place is
    `apply_table`'s job.

    All or nothing. Every missing `.dist` is checked before ANY of them is moved, because
    a loop that moved as it went would leave the earlier files behind on a table the image
    disagrees with — and since a file that exists is never re-copied, the next resume
    would sail straight past them into a half-built `etc/`.

    `copy_from_image`'s own failures are re-raised untouched: see `CopyFromImage`.

    The whole-directory copy carries one constraint, measured rather than assumed (Engine
    29.6.2, Windows 11, ordinary account): `docker cp` recreates each entry of the tar it
    extracts, and creating a SYMLINK on Windows needs a privilege a normal account does
    not hold — so one symlink anywhere in `source_dir` fails the copy, and the conf files
    beside it do not arrive. Upstream is safe today: `mangos-tbc`'s `mangosd` and `realmd`
    `CMakeLists.txt` install every conf with plain `install(FILES ... ${CONF_DIR})`, and
    the one `install(DIRECTORY ...)` beside them targets `BIN_DIR`. If that ever changes,
    the answer is a per-file `docker cp`, which is what the round-trip count above bought
    us out of. Filed in `pyplan/bug-checklist.md`.

    Returns the files created, in table order.

    Raises:
        InstallerError: the image does not ship a `.dist` the table names.
    """
    missing = [name for name in table.files if not (etc_dir / name).exists()]
    if not missing:
        return ()
    etc_dir.mkdir(parents=True, exist_ok=True)
    staging = etc_dir / _STAGING_DIR
    _clear(staging)
    # `docker cp <c>:/opt/mangos/etc/ <dest>` copies the directory's CONTENTS while
    # `.../etc <dest>` copies the directory itself, and `staging` does not exist, so the
    # trailing slash decides whether the files land in it or beside it. The catalog is
    # free to write `source_dir` either way, and the difference only shows against a real
    # daemon; it is settled here instead.
    source_dir = table.source_dir.rstrip("/")
    created: list[Path] = []
    try:
        copy_from_image(image_ref, source_dir, staging)
        dists = [(name, staging / f"{name}{DIST_SUFFIX}") for name in missing]
        for name, dist in dists:
            if not dist.is_file():
                raise InstallerError(
                    f"the built image {image_ref} does not contain "
                    f"{source_dir}/{name}{DIST_SUFFIX}, so {name} could not be created. "
                    "The catalog's conf table and the image disagree; that is a bug in "
                    "the app."
                )
        for name, dist in dists:
            target = etc_dir / name
            shutil.move(str(dist), str(target))
            os.chmod(target, CONF_MODE)
            created.append(target)
            logger.info(f"materialised {target} from {image_ref}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return tuple(created)


def apply_table(
    table: ConfPatchTable, etc_dir: Path, tokens: Mapping[str, str]
) -> tuple[Path, ...]:
    """Patch every file in the table in place; return the ones whose bytes changed.

    Written only on change, so a resume over a finished install moves no mtime — which is
    what lets a user tell at a glance whether Yu'lon has rewritten their conf — and a
    user's own additions, keys the table has never heard of, survive untouched.

    Each file is read ONCE and the patched text compared against that same read. Reading
    it again to decide whether to write is how two answers to one question get to
    disagree, and the comparison is on the exact bytes for the same reason every ending
    assertion in this module is: `read_text()` performs the very newline translation a
    bug here would introduce, so a check built on it reports "unchanged" about a file it
    is quietly converting. `composegen.write_plan` has that defect live today.

    A missing file is an error, not a copy: `materialise()` is the one place a conf comes
    from, and reaching here without it is a wiring bug rather than something a user can
    act on.

    Returns the files changed, in table order.

    Raises:
        InstallerError: a file could not be read or written, or a value could not be
            filled (see `patch`).
    """
    changed: list[Path] = []
    for name, table_patch in table.files.items():
        path = etc_dir / name
        before = _read(path)
        after = patch(before, table_patch, tokens)
        if after == before:
            continue
        _write(path, after)
        logger.info(f"patched {path}")
        changed.append(path)
    return tuple(changed)


def _line(key: str, raw: str, tokens: Mapping[str, str]) -> str:
    """The whole `Key = value` line, refused if it would be more than one line.

    A value containing a newline is the quietest bad thing this module could do: it
    writes one key the catalog asked for and one it never mentioned, as valid ini the
    emulator then obeys. Checked on the FILLED text, so a token cannot smuggle one in.
    """
    line = f"{key} = {_value(key, raw, tokens)}"
    if "\n" in line or "\r" in line:
        raise InstallerError(
            f"the conf value for {key} contains a line break, which would write a second "
            "key into the file. That is a bug in the catalog, not something to fix on "
            "this machine."
        )
    return line


def _value(key: str, raw: str, tokens: Mapping[str, str]) -> str:
    """The value with its tokens filled; an unknown token names the key that carried it."""
    try:
        return fill(raw, tokens)
    except ComposeGenError as exc:
        raise InstallerError(f"the conf value for {key} could not be filled in: {exc}") from exc


def _split_ending(line: str) -> tuple[str, str]:
    """(body, line ending) so a rewritten line keeps the ending it had."""
    body = line.rstrip("\r\n")
    return body, line[len(body) :]


def _newline_of(lines: list[str]) -> str:
    """The file's own line ending, judged from its first line; `\\n` for an empty file.

    The FIRST line, not the last: the case that needs the answer most is a file whose
    last line has no ending at all to copy.
    """
    if lines and lines[0].endswith("\r\n"):
        return "\r\n"
    return "\n"


def _clear(path: Path) -> None:
    """Remove a leftover staging entry, whether an interrupted run left a dir or a file.

    `shutil.rmtree` raises on a plain file, and leaving one behind is not harmless: the
    copy would then fail, or — for a directory — land INSIDE it, at
    `.yulon-conf-dist/etc/`, where no `.dist` is ever found. Every resume would report
    that the image does not ship a file the image does ship.
    """
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _read(path: Path) -> str:
    """The file's exact text, line endings included (no universal-newline translation).

    `newline=""` is load-bearing twice over: it keeps a CRLF conf CRLF through the patch,
    and it keeps the unchanged-file comparison in `apply_table` honest about the bytes on
    disk rather than about their translation.

    This is where "empty" and "could not be read" stop being the same answer. Empty text
    is a conf with no keys in it and comes back as `""`; anything that stopped the read
    is an `InstallerError` naming the path. `UnicodeDecodeError` is caught alongside
    `OSError` because it is a `ValueError` and would otherwise escape an install as a
    traceback about a byte offset — a user's editor saving one accented comment as cp1252
    is all it takes.
    """
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise InstallerError(
            f"{path} could not be read ({exc}), so it was not patched. It should have been "
            "copied out of the image by the conf stage."
        ) from exc


def _write(path: Path, text: str) -> None:
    """Write the text as given, owner-only, and never leave the conf half-written.

    The new text goes to a temporary file beside the conf and is renamed over it, so what
    is on disk is either entirely the old conf or entirely the new one. Straight into the
    file would truncate it first, and a run interrupted at that moment leaves a conf no
    resume ever repairs — `materialise()` will not re-copy a file that exists, so the
    server reads the surviving half, silently takes defaults for everything past the cut,
    and boots looking healthy.

    The rename is within `path`'s own directory so it is atomic, and the mode is set on
    the temporary file before the rename rather than after, so the conf is never readable
    by anyone else even for an instant (the database password is in it). `newline=""` for
    the same reason `_read` has it: text mode on Windows would turn every `\\n` into
    `\\r\\n` and convert an LF conf on a developer's machine.

    `UnicodeEncodeError` is caught beside `OSError` for the reason `_read` catches
    `UnicodeDecodeError`: it is a `ValueError` too, so the obvious `except OSError` misses
    it and a resume ends in a raw traceback about a character position in a file it does
    not name — the same defect one function away, given the same answer. It is reachable
    rather than theoretical: `json.loads` accepts a lone surrogate, so a `"\\ud800"` in a
    `catalog.json` conf value survives `fill()` and `_line`'s line-break check (a
    surrogate is not a newline) and arrives here as text UTF-8 cannot encode.

    Caught SPECIFICALLY, not as `ValueError` and not as `Exception`. `UnicodeEncodeError`
    is the only `UnicodeError` a write can raise, so the parent class buys nothing; and
    anything else escaping this block is a bug in the caller or in this module rather than
    a state of the machine — a non-`str` `text` is a `TypeError`, and the `encoding` and
    `newline` whose spelling could raise `LookupError` or `ValueError` are literals on the
    line above. Reporting one of those as "the conf could not be written" would dress a
    seam bug up as a bad file, which is the mistake this project is briefed against.
    """
    tmp = path.with_name(f"{path.name}{_TEMP_SUFFIX}")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, CONF_MODE)
        os.replace(tmp, path)
    except (OSError, UnicodeEncodeError) as exc:
        raise InstallerError(
            f"{path} could not be written ({exc}), so it was left as it was."
        ) from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError as exc:  # pragma: no cover - a stray temp file is not fatal
                logger.warning(f"could not remove {tmp}: {exc}")
