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
it, and all of them are appended. The read/unreadable distinction belongs to
`materialise()`, on the side of the seam that does the opening.

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

import re
from collections.abc import Mapping

from yulon.catalog.catalog import ConfPatch
from yulon.catalog.composegen import ComposeGenError, fill
from yulon.catalog.installer import InstallerError

CONF_MODE = 0o600
"""Every conf this module writes is owner-only: the database password is in it."""

DIST_SUFFIX = ".dist"
"""What the images append to a template conf; stripped when the file is materialised."""


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
