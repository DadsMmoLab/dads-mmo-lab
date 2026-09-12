"""What kind of line arrived, and what it says once its prefix is off (T35).

The install engine, the git seam and every relayed subprocess all yield plain
strings into one `LogPanel`, and until T35 they arrived indistinguishable: the
97 minutes of `[Map 230] Building tile [32,32] (08 / 12)` the T30 install wrote
had the same weight as "Cloning …". This module is the one place that answers
"what is this line" — a pure classifier, no Qt, so the engine side can build a
line the panel side will understand without either importing the other's world.

Two prefixes carry what a line's own text cannot say: that a line is a
subprocess's output rather than the app's own sentence (`TOOL`), and that a line
is a progress reading for the header strip rather than something to append
(`PROGRESS`). Everything else is recognised BY SHAPE, because the engine's own
`Step N of M (P%): <name>` and `--- <name>` lines are what gate scripts, log
captures and the interrupted-import watchers grep, and a prefix on those would
have moved a format the whole project matches on.

`\\x1e` (RECORD SEPARATOR) begins both prefixes: it is not printable, so nothing
git, BuildKit, cmake or a shell script emits contains one, and a line that
somehow did would be classified rather than lost — see `parse()`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

PROGRESS = "\x1eprogress "
"""Marks a progress reading: `PROGRESS + "<stage> <percent|-> <text>"`.

Not appended by the panel. `-` where the source gave no number, because a busy
bar and a bar at 0% say different things and a progress line with no percent is
the first kind.
"""

TOOL = "\x1etool "
"""Marks one line of a subprocess's output, relayed as it arrived."""

Kind = Literal["stage", "marker", "sentence", "tool", "warning", "failure", "progress"]


@dataclass(frozen=True)
class Parsed:
    """One line, classified. `text` is what a reader should see — never the prefix."""

    kind: Kind
    text: str
    stage: str = ""
    percent: int | None = None


@dataclass(frozen=True)
class Step:
    """The three fields of a `Step N of M (P%): <name>` line, for the header strip."""

    number: int
    total: int
    name: str


_STEP = re.compile(r"^Step (\d+) of (\d+)(?: \(\d+%\))?: (.*)$")
_STAGE = re.compile(r"^Step \d+ of \d+")
_MARKER = re.compile(r"^--- ")
_FAILURE = re.compile(r"\bFAILED\b|^INSTALL FAILED|^error:|\berror\b.*:|\[fail\]|refused")
_WARNING = re.compile(r"^warning:|:\s+warning:|\[warn\]|\bWARNING\b")
"""The amber shapes, and the second alternative is not one of T35's three.

`^warning:` alone cannot see a compiler, and the compiler is the reason this
tone exists: gcc and clang both write `mmaps.cpp:88:5: warning: …`, so an
anchored pattern left every real warning in a two-hour build dimmed as ordinary
tool chatter. `_FAILURE` needed no such addition — `\berror\b.*:` is unanchored
and already matches `collect2: error: …` where `^error:` would not.
"""


def _tone(text: str) -> Kind | None:
    """`failure`, `warning`, or neither — the two tones a line's own words can claim.

    Failure is asked first so one line gets one tone: "WARNING: the import
    FAILED" is a failure, and painting it amber would say the softer of the two
    things it says.
    """
    if _FAILURE.search(text):
        return "failure"
    if _WARNING.search(text):
        return "warning"
    return None


def _progress(payload: str) -> Parsed | None:
    """`<stage> <percent|-> <text>` as a `Parsed`, or None when it is not that shape."""
    parts = payload.split(" ", 2)
    if len(parts) < 2:
        return None
    stage, percent = parts[0], parts[1]
    text = parts[2] if len(parts) == 3 else ""
    if percent == "-":
        return Parsed("progress", text, stage=stage, percent=None)
    if not percent.isdigit():
        return None
    return Parsed("progress", text, stage=stage, percent=int(percent))


def parse(line: str) -> Parsed:
    """Classify one line and hand back what should be shown for it.

    Order is the whole specification, so it is spelled out here rather than left
    to be read off the branches:

    1. `PROGRESS` — a progress reading. A payload that will not parse becomes a
       `sentence` WITH THE PREFIX STRIPPED, never a dropped line: the panel does
       not append progress lines, so a malformed one classified as progress
       would be a line the engine yielded and nobody ever saw.
    2. `TOOL` — relayed subprocess output, unless the payload's own words claim
       a tone. A compiler's `warning:` is still amber, because the reason tool
       output is dimmed is that most of it is chatter, and that line is not.
    3. The engine's own shapes: `Step N of M`, then `--- `.
    4. The two tones, failure before warning.
    5. Anything else is one of the app's own sentences.
    """
    if line.startswith(PROGRESS):
        payload = line[len(PROGRESS) :]
        return _progress(payload) or Parsed("sentence", payload)
    if line.startswith(TOOL):
        payload = line[len(TOOL) :]
        return Parsed(_tone(payload) or "tool", payload)
    if _STAGE.match(line):
        return Parsed("stage", line)
    if _MARKER.match(line):
        return Parsed("marker", line)
    return Parsed(_tone(line) or "sentence", line)


_BUILDKIT_STEP = re.compile(r"^#\d+ \[(?:[^\]]* )?(\d+)/(\d+)\]")
"""BuildKit's own progress, which is a step count and never a percentage.

The number of the Dockerfile step being run out of that stage's total, and the
only figure a `docker compose build --progress plain` produces. The build is the
longest stage an install has.

**Both spellings, because the stage's name is optional.** A multi-stage
Dockerfile prints `#10 [builder 5/8]` and a single-stage one prints `#5 [2/4]`.
Every template this app generates today is multi-stage, so every step line in
`pyplan/gates/` carries a name — but requiring the space that follows one made
the other shape unreadable for no reason at all, and a template that grew a
single-stage variant would have gone silently unread (review, 2026-09-12).
"""

_COMPILER_PERCENT = re.compile(r"\[\s*(\d+)%\]")
"""CMake's own counter, and it moves INSIDE one BuildKit step.

`[ 43%] Building CXX object src/x.cpp.o`. One BuildKit step covers the whole
`RUN cmake --build`, which on this project is hours, so where both numbers are
in one line this is the finer of the two and is read second.
"""

_MMAP_TILE = re.compile(r"Building tile \[\d+,\d+\] \((\d+) / (\d+)\)")
_MMAP_MAP = re.compile(r"\[Map (\d+)\]")
"""The mmap generator's tile counter, per map.

`[Map 000] Building tile [22,52] (01 / 741)` — 8005 of them in
`pyplan/gates/7.7-win11-tortoise/tortoise77.log`, and 97 minutes of them in the
T30 install this ticket was filed from. The percentage is within the MAP,
because that is what the two numbers in the line are; the generator never says
how many maps there are.
"""


def _percent_of(part: str, whole: str) -> int | None:
    """`part` of `whole` as a whole percentage, or None when there is no whole.

    `[0/0]` is a real BuildKit line for a stage with nothing to do, and a
    division here would raise on the output thread of a running install.
    """
    total = int(whole)
    return None if total <= 0 else int(part) * 100 // total


def relayed(line: str, *, stage: str) -> list[str]:
    """One line of a subprocess's output, as the records the panel should get for it.

    Always the line itself, marked `TOOL`, because most of what a build, an
    extractor or a database import prints is chatter that should read as chatter
    — and because a line is not consumed by having a number in it. Then, where
    the line does carry a figure saying how far along something is, a second
    `PROGRESS` record that moves the header strip. In that order: the panel
    shows the line before the strip claims to describe it.

    **Two records and not one, which is the review finding of 2026-09-12.** This
    returned the `PROGRESS` record ALONE, and it cost the raw line twice over:
    gone from the panel, because progress records are not appended, and gone
    from the gate transcript, because `install_wiring` writes the display text
    of what the engine yields — so `[Map 000] Building tile [22,52] (01 / 741)`,
    which the T30 gate grepped out of its log, was deleted by the change meant
    to make it readable. Keeping both costs one dimmed line per reading, which
    is what the dimming is for.

    Three sources carry a figure, and they are tried in that order with the
    LATER winning — see each pattern for what it reads and why the compiler's
    beats BuildKit's.

    `stage` names what is being relayed, for the progress record's own field. It
    is deliberately NOT the family's `Stage` name, which the relay cannot know
    (the family binds it), and nothing reads it as one.
    """
    reading = _reading(line, stage)
    return [TOOL + line] if reading is None else [TOOL + line, reading]


def _reading(line: str, stage: str) -> str | None:
    """The `PROGRESS` record `line` carries, or None if it carries no figure."""
    tile = _MMAP_TILE.search(line)
    if tile is not None:
        percent = _percent_of(tile.group(1), tile.group(2))
        if percent is not None:
            # `(this map)` is not decoration. The generator never says how many
            # maps it will do — `MmapPlan` carries an argv, a file floor and a
            # `required` flag, and MoveMapGen discovers the maps from
            # `data/maps` itself and skips the ones it already has output for —
            # so this figure is a fraction of ONE map and the bar goes back to
            # zero at the next. Measured in the 7.7 Tortoise transcript: map 000
            # ends at 741 tiles, map 001 starts at 1 of 1018. Without those two
            # words a reader watches a stage bar fall from 99% to 0% forty times
            # and reads each as a failure (review, 2026-09-12).
            said = f"tile {int(tile.group(1))} of {int(tile.group(2))} (this map)"
            found_map = _MMAP_MAP.search(line)
            if found_map is not None:
                said = f"Map {found_map.group(1)} · {said}"
            return PROGRESS + f"{stage} {percent} {said}"
    percent = None
    step = _BUILDKIT_STEP.match(line)
    if step is not None:
        percent = _percent_of(step.group(1), step.group(2))
    compiler = _COMPILER_PERCENT.search(line)
    if compiler is not None:
        percent = int(compiler.group(1))
    return None if percent is None else PROGRESS + f"{stage} {percent} {line}"


def parse_step(line: str) -> Step | None:
    """The stage line's own three fields, or None for any other line.

    Read off the engine's format rather than counted here: the spine is the only
    thing that knows how many stages this run has, and a resumed install or a
    rebuild has a different total from a first install's.
    """
    found = _STEP.match(line)
    if found is None:
        return None
    return Step(int(found.group(1)), int(found.group(2)), found.group(3))
