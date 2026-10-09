"""A settings card for a module that declares no keys, made from its `.conf.dist` (T590).

A module added by link or folder is derived by `module_source`: its `conf/*.conf.dist`
is copied into place as the live `.conf`, and the manifest declares no keys for it, so
the Tuning tab had a raw editor for it and no card. This reads the `.conf.dist` when the
tab builds its cards and turns it into the same `tuning.TuningRow`s a declared module
gets: each active `Key = default` line is a row, the comment above it is the help, and
the card is saved through `tuning.write` (byte-preserving, a backup first, no link out of
the server folder), exactly as every other card.

**The source.** The `.conf.dist` BESIDE the live conf (`env/dist/etc/modules/<name>.conf.dist`,
which the server build puts there and is the file the running server was built from), then
the `conf/` template in the module's own clone. A file that is a link out of the server
folder, is too big for the raw editor, or is not UTF-8 is not read.

**Whose conf.** Only an INSTALLED module's conf, directly in the modules folder, that the
manifest names with no keys, that no other card already declares keys for, and that is not
one of the server's own confs. The live `.conf` must already be there: a Save must never
create the module's conf, because the install lays it. Nothing is invented for a conf with
no `.conf.dist`, or none that has a key in it: no card.

**The styles it reads** (measured on real AzerothCore modules, 2026-10-09):

* a block above the key, a blank line between or none -- the usual
  `#    Key` / `#        Description: ...` / `#        Default:     1 - (Enabled)` entry
  (mod-aoe-loot, mod-solo-lfg, mod-1v1-arena, mod-reward-shop, mod-unbound);
* plain prose lines above the key (mod-learn-spells, mod-npc-beastmaster);
* every key described in one block at the top and the `Key = value` lines far below it
  (mod-transmog, mod-cfbg, mod-ale), found by the key's name;
* an entry whose description has no `Description:` label and whose default has no colon
  (mod-ah-bot), and one whose description is prose under the key's name with the default
  last (mod-dungeon-clear);
* a quoted text value, `true`/`false`, a decimal, and a number.

**The safety rule is `tuning`'s.** A type is given only where the default makes it certain,
and an unsure case degrades to a text box: `0` and `1` make a switch unless the comment
names another number (a key that takes 0 to 4 and defaults to 0 is not a switch); a whole
number is a number unless the comment speaks of decimals (`Rate = 1` may take 1.5) or it
does not fit 32 bits, and a default of 0 or more is let through up to the `uint32` largest
unless the comment shows a negative (`reads_unsigned`); everything else is text. No range is
invented, and none is read from the prose. The help text is the module author's own words,
never reworded.

Nothing here imports Qt, and nothing here writes a file.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from yulon import tuning
from yulon.log import get_logger
from yulon.manifest import ConfKey, Manifest
from yulon.manifest_store import FAMILY_FILES

logger = get_logger(__name__)

HELP_LIMIT = 600
"""The most characters of a key's comment a row shows; the raw editor holds the rest."""

DIST_SUFFIX = ".dist"


@dataclass(frozen=True)
class DistKey:
    """One `Key = default` line of a `.conf.dist`, with the comment above it and a type."""

    key: str
    default: str
    help: str | None
    type: str | None


_WHOLE = re.compile(r"-?(?:0|[1-9][0-9]*)")
"""A whole number spelled the one plain way: no plus, no leading zero, ASCII digits only."""

_OTHER_NUMBER = re.compile(r"(?<![\w.])([0-9]+)(?!\w|\.[0-9])")
"""A whole number standing alone in prose (not part of a word, a version or a decimal)."""

_DECIMALISH = re.compile(
    r"[0-9]\.[0-9]|\b(?:rate|multiplier|factor|fraction|decimal|float|percent(?:age)?|ratio)\b",
    re.IGNORECASE,
)
"""Prose that says a value may carry a decimal point, so a whole-number default is not proof."""

_LABEL = re.compile(r"^(description|default|range)\b\s*:\s*(.*)$", re.IGNORECASE)
_LABEL_NO_COLON = re.compile(r"^(default)\s+(?=[0-9\"'(]|true\b|false\b|on\b|off\b)(.*)$", re.I)
"""`Default 0 (disabled)`, as mod-ah-bot spells it: a value-looking word after `Default`."""


def parse(text: str) -> tuple[DistKey, ...]:
    """Every active assignment in a `.conf.dist`, in the file's order, once each.

    The line rule is `tuning.conf_value`'s: a trimmed line that is empty, `#` or `[` says
    nothing, the key is what comes before the first `=`, the value is the rest, and the
    FIRST copy of a key is the one the core reads.
    """
    lines = text.splitlines()
    found: dict[int, tuple[str, str, bool]] = {}
    names: set[str] = set()
    for index, line in enumerate(lines):
        if tuning._is_conf_comment(line):
            continue
        head, sep, tail = line.partition("=")
        key = head.strip()
        if not sep or not key:
            continue
        value = tail.strip()
        first = key not in names
        names.add(key)
        found[index] = (key, value, first)
    runs: list[list[str]] = []
    attached: dict[int, list[str]] = {}
    block: list[str] = []
    pending: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            if _is_rule(stripped):
                if block:
                    runs.append(block)
                block, pending = [], []
            else:
                block.append(stripped[1:])
            continue
        here = block or pending
        if block:
            runs.append(block)
        if stripped == "":
            # A blank line ends the block but leaves it waiting for the key below it.
            pending, block = here, []
            continue
        if index in found:
            attached[index] = here
        pending, block = [], []
    if block:
        runs.append(block)
    docs: dict[str, list[str]] = {}
    for run in runs:
        for name, body in _entries(run, names):
            docs.setdefault(name, body)
    rows: list[DistKey] = []
    for index, (key, value, first) in found.items():
        if not first:
            continue
        here = attached.get(index, [])
        words = _help_of(key, here, docs, names)
        default = value.strip('"')
        rows.append(DistKey(key, default, words, _type_of(key, value, words)))
    return tuple(rows)


def _is_rule(stripped: str) -> bool:
    """A decorative line such as `#######` or `# ------`: it ends the block above it."""
    core = stripped.replace("#", "").strip()
    return len(stripped) >= 3 and (core == "" or (core[0] in "=-*~_" and len(set(core)) == 1))


def _entries(run: Iterable[str], names: Collection[str]) -> list[tuple[str, list[str]]]:
    """The `Key` / body entries of one comment block: a line that is a key's name starts one.

    A line is a heading when it is exactly the name of a key the file assigns, or a dotted
    name with the lines below it indented deeper (a module may spell the heading a little
    differently from the key under it: mod-1v1-arena).
    """
    lines = list(run)
    entries: list[tuple[str, list[str]]] = []
    for n, body in enumerate(lines):
        word = body.strip()
        if tuning._PLAIN_KEY.match(word) and (word in names or _heads_an_indented_body(lines, n)):
            entries.append((word, []))
        elif entries:
            entries[-1][1].append(body)
    return entries


def _heads_an_indented_body(lines: list[str], n: int) -> bool:
    word = lines[n].strip()
    if "." not in word:
        return False
    indent = len(lines[n]) - len(lines[n].lstrip())
    for below in lines[n + 1 :]:
        if below.strip():
            return len(below) - len(below.lstrip()) > indent
    return False


def _help_of(
    key: str, block: list[str], docs: Mapping[str, list[str]], names: Collection[str]
) -> str | None:
    """The module author's words about `key`: its own entry by name, else the block above it."""
    if key in docs:
        return _help(docs[key])
    entries = _entries(block, names)
    if entries:
        # One entry right above, headed by a name the file does not assign, is this key's
        # (a heading spelled differently from the key); a name another key owns is not.
        if len(entries) == 1 and entries[0][0] not in names:
            return _help(entries[0][1])
        return None
    return _help(block)


def _help(lines: Iterable[str]) -> str | None:
    """The description of one entry or block: its prose, a stated range, never the default."""
    description: list[str] = []
    ranges: list[str] = []
    into = description
    for body in lines:
        word = body.strip()
        if word == "":
            into = description
            continue
        match = _LABEL.match(word) or _LABEL_NO_COLON.match(word)
        if match:
            label = match.group(1).lower()
            into = {"description": description, "range": ranges}.get(label, [])
            word = match.group(2).strip()
        if word:
            into.append(word)
    text = " ".join(description)
    if ranges:
        text = f"{text} Range: {' '.join(ranges)}".strip()
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) > HELP_LIMIT:
        text = text[:HELP_LIMIT].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return text


_NEGATIVE_IN_PROSE = re.compile(r"(?<![\w.])-[0-9]")
"""A negative number written in a comment (`-1 for no limit`): the key is read as signed."""


def reads_unsigned(default: str, words: str | None) -> bool:
    """Whether a whole-number key is checked as a `uint32`: default 0 or more, no negative shown.

    A default says nothing about signedness (`AuctionHouseBot.GUID = 0` is a `uint32`),
    so a key that starts at 0 or above is let through up to 4294967295, which the signed range
    would refuse. A comment that shows a negative value (`-1` for no limit) keeps the signed
    range. A negative number given where 0 is meant is refused: the raw editor is the way round.
    """
    return int(default) >= 0 and not _NEGATIVE_IN_PROSE.search(words or "")


_TOGGLE_IN_PROSE = re.compile(
    r"\b(?:enable[ds]?|disable[ds]?|on|off|true|false|yes|no|toggle|whether)\b", re.IGNORECASE
)
_TOGGLE_IN_NAME = re.compile(r"enable|disable|debug|trace|announce|allow", re.IGNORECASE)
_A_QUANTITY = re.compile(
    r"\b(?:how many|interval|seconds?|minutes?|hours?|days?|delay|timeout|count|number of|"
    r"amount|maximum|minimum|limit|level)\b",
    re.IGNORECASE,
)
"""What shows that a key defaulting to 0 or 1 is a toggle, and what shows it is a quantity."""


def _is_a_toggle(key: str, prose: str) -> bool:
    """Whether a 0/1 default is a switch: the comment or the name says on/off, and no other number.

    A default of 0 or 1 alone proves nothing (`AuctionHouseBot.GUID = 0`, `ALE.AutoReloadInterval
    = 1`), so a switch needs a word that says so (enable, off, true, a `0 ... 1` pair) or a
    name that does, and the comment may not name another number or call it a quantity.
    """
    if any(int(n) not in (0, 1) for n in _OTHER_NUMBER.findall(prose)) or _A_QUANTITY.search(prose):
        return False
    numbers = {int(n) for n in _OTHER_NUMBER.findall(prose)}
    return bool(_TOGGLE_IN_PROSE.search(prose) or numbers == {0, 1} or _TOGGLE_IN_NAME.search(key))


def _type_of(key: str, raw: str, words: str | None) -> str | None:
    """`bool`, `int` or `None` (a text box), only where the default makes it certain."""
    if not _WHOLE.fullmatch(raw):
        return None
    prose = words or ""
    smallest, largest = tuning.int_range(reads_unsigned(raw, prose))
    if not smallest <= int(raw) <= largest or _DECIMALISH.search(prose):
        return None
    return "bool" if raw in ("0", "1") and _is_a_toggle(key, prose) else "int"


def rows_for(
    manifests: Iterable[Manifest],
    installed: Mapping[str, frozenset[str]],
    server_dir: Path,
    *,
    core_files: Collection[str],
    declared_files: Collection[str],
    clone_dir: Callable[[Manifest], Path],
) -> tuple[tuning.TuningRow, ...]:
    """The rows of every installed module conf that declares no keys, in the tab's order.

    `core_files` is the server's own confs (never a card), `declared_files` every file a
    card already has declared keys for, and `clone_dir` where a manifest's clone is. The
    order is `tuning.rows_for`'s: family by family, the catalog's order inside one, and the
    manifest's own conf order.
    """
    rows: list[tuning.TuningRow] = []
    taken: list[str] = []
    catalog = list(manifests)
    for kind in FAMILY_FILES:
        here = installed.get(kind, frozenset())
        for manifest in catalog:
            if manifest.type != kind or manifest.id not in here:
                continue
            for conf in manifest.conf:
                if conf.keys or not _is_module_conf(conf.file):
                    continue
                if tuning.is_one_of(conf.file, [*core_files, *declared_files, *taken], server_dir):
                    continue
                keys, text = _read_card(manifest, conf.file, conf.template, server_dir, clone_dir)
                if not keys or text is None:
                    continue
                taken.append(conf.file)
                for item in keys:
                    rows.append(
                        tuning.TuningRow(
                            module_id=manifest.id,
                            module_name=manifest.name,
                            family=manifest.type,
                            file=conf.file,
                            key=item.key,
                            label=item.key,
                            explain=item.help,
                            type=item.type,
                            min=None,
                            max=None,
                            default=item.default,
                            current=tuning.conf_value(text, item.key),
                            installed=True,
                            backend="conf",
                            read_only_reason=tuning._read_only_reason(conf.file, "conf", item.key),
                        )
                    )
    return tuple(rows)


def _is_module_conf(file: str) -> bool:
    """A `.conf` directly in the modules folder: the one place a module's own conf is."""
    if tuning.backend_of(file) != "conf" or tuning._is_glob(file) or "\\" in file:
        return False
    path = PurePosixPath(file)
    return str(path.parent) == tuning.MODULE_CONF_DIR and path.name != ""


def _read_card(
    manifest: Manifest,
    file: str,
    template: str | None,
    server_dir: Path,
    clone_dir: Callable[[Manifest], Path],
) -> tuple[tuple[DistKey, ...], str | None]:
    """The dist's keys and the live conf's text, or `((), None)` when there is no card."""
    text = _plain_text(server_dir / file, server_dir)
    if text is None:
        return (), None
    sources = [server_dir / f"{file}{DIST_SUFFIX}"]
    if template is not None and ".." not in PurePosixPath(template).parts:
        sources.append(clone_dir(manifest) / template)
    for source in sources:
        dist = _plain_text(source, server_dir)
        keys = parse(dist) if dist is not None else ()
        if keys:
            return keys, text
    return (), None


def _plain_text(path: Path, server_dir: Path) -> str | None:
    """`path`'s text when it is a plain file inside the server folder that may be read.

    Not a link out of the server folder (`tuning.check_inside`), not bigger than the raw
    editor opens, and UTF-8 (`tuning._read`); anything else answers `None`.
    """
    try:
        tuning.check_inside(path, server_dir)
        if not path.is_file() or path.stat().st_size > tuning.MAX_EDIT_BYTES:
            return None
    except (tuning.TuningError, OSError) as exc:
        logger.debug(f"conf_dist: {path} is not read: {exc}")
        return None
    return tuning._read(path)


def conf_keys(
    rows: Iterable[tuning.TuningRow], family: str, module_id: str, file: str
) -> dict[str, ConfKey]:
    """The `tuning.check` declarations of one card's file, from the rows drawn for it."""
    return {
        row.key: ConfKey(
            key=row.key,
            default=row.default,
            explain=row.explain,
            type="bool" if row.type == "bool" else "int" if row.type == "int" else None,
            unsigned=row.type == "int"
            and row.default is not None
            and reads_unsigned(row.default, row.explain),
        )
        for row in rows
        if (row.family, row.module_id, row.file) == (family, module_id, file)
    }
