"""Every module setting this install has, and the file behind it (T43).

Three things live here and none of them knows what a widget is: the READER
(`rows_for`), which turns this game's manifests plus what is on disk into an
ordered list of settings; the WRITER (`write`), which changes named keys inside
a conf file and leaves everything else byte-for-byte; and the GUARD (`lint`),
the cheap "does this still look like a `.conf`?" pass the raw editor shows while
somebody types. `yulon/ui/widgets/tuning_panel.py` draws them and decides
nothing, exactly as `modules_panel.py` draws T42's rows.

**Why this exists.** Counted across `manifests/wow-wotlk`: 24 manifests declare
conf keys, 107 keys in all, and 73 of them carry no `default`. `apply.py`'s
`_conf()` declines to write a key with no default on purpose -- "which value
belongs in a user's core configuration is the catalog's sentence to write" --
so those 73 were settings the catalog named, the applier skipped, and nobody
could reach from inside the app. Installing `mod-transmog` on the live WotLK
box (`yulon-win11`, 2026-09-12) reported five of them in one line.

**The safety rule, which every ambiguity here is resolved by.** A key with no
`type` is a TEXT BOX -- never a switch, never a spinner. No bound is invented
where `min`/`max` are absent. A value that fails its own type is refused before
a byte is written. And `current` is never fabricated: a file that is missing or
unreadable answers `None`, and the row still lists with its default.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from yulon.log import get_logger
from yulon.manifest import ConfKey, Manifest
from yulon.manifest_store import FAMILY_FILES

logger = get_logger(__name__)

Backend = Literal["conf", "lua", "other"]
"""Where a declared key actually lives.

`conf` is a worldserver-style `Key = Value` file the app may rewrite. `lua` is a
deployed script whose assignments the installer patches in place -- out of v1 by
T43 decision 5, because Yu'lon's ALE story differs enough from DML's to want its
own ticket. `other` is everything a `ConfFile.file` can name that is not a file
at all: `manifests/wow-wotlk/ale/paragon.json` points five keys at
`acore_ale.paragon_config (DB table)`.
"""

LUA_IS_NOT_IN_V1 = (
    "This setting lives in a deployed Lua script, not in a .conf file. Yu'lon shows it "
    "here so it is not hidden, but does not write it yet — patching a running ALE script "
    "is its own job with its own restart rules. Re-install the script to change it."
)
"""T43 decision 5, in the words the row shows. Listed, read-only, and never dropped."""

NOT_A_CONF_FILE = (
    "The catalog points this key at `{file}`, which is not a file on disk — it is stored "
    "somewhere else (a database table). Yu'lon can name it here but cannot edit it."
)

MORE_THAN_ONE_FILE = (
    "The catalog points this key at `{file}`, which matches more than one file. There is "
    "no single file to show or to write, so this one is read-only."
)

NOT_ONE_KEY = (
    "The catalog names `{key}`, which is shorthand for a GROUP of settings rather than one "
    "key — Yu'lon would not know which line to write. Edit them in the file itself, on the "
    "right."
)
"""A `ConfKey.key` that is not one key at all.

Six of the 107 shipped keys are like this, and every one of them was written as
a note to a reader rather than as something to write: `AutoBalance.Enable.*` is
eleven real keys, `FillRateCommon / FillRateRare / FillRateUltra` is three, and
`common/rare/ultraRare_*_price` is a naming pattern. Found while enriching the
manifests for T43 point 7, and it matters because before T43 nothing WROTE a
key with no default — these six were only ever printed in a skip line.
"""

CONF_SUFFIX = ".conf"
LUA_SUFFIX = ".lua"

_PLAIN_KEY = re.compile(r"^[A-Za-z0-9_.\-]+$")
"""What a real conf key looks like: the character set every one in this catalog uses.

An allow-list and not a deny-list of `*` and `/`, because the question is "is
this one key?" and the honest answer for anything outside this set is "we cannot
tell" -- which has to degrade to read-only, the same way an unknown `type`
degrades to a text box.
"""


def _is_glob(path: str) -> bool:
    """`apply._is_glob`'s rule, spelled again rather than imported.

    `apply` imports half the app (docker, git, the runner); this module is
    imported by a panel and by tests that want neither. Six characters of
    duplication against a dependency edge that would drag a subprocess seam into
    a pure reader.
    """
    return any(ch in path for ch in "*?[")


def backend_of(file: str) -> Backend:
    """Which kind of thing the manifest's `file` names, by its own spelling."""
    low = file.lower()
    if low.endswith(CONF_SUFFIX):
        return "conf"
    if low.endswith(LUA_SUFFIX):
        return "lua"
    return "other"


def _read_only_reason(file: str, backend: Backend, key: str = "x") -> str | None:
    """Why this row cannot be written, or `None` when it can.

    Ordered by what blocks hardest: a `key` that is not one key is asked about
    first, because it is true whatever file it points at. A glob has no single file at all, so it
    could not be written even if its backend were writable; the Lua sentence
    comes next because it is the one a user is most likely to go looking for
    (`accountwide/*.lua` is both, and either answer would be true).
    """
    if not _PLAIN_KEY.match(key):
        return NOT_ONE_KEY.format(key=key)
    if _is_glob(file):
        return MORE_THAN_ONE_FILE.format(file=file)
    if backend == "lua":
        return LUA_IS_NOT_IN_V1
    if backend == "other":
        return NOT_A_CONF_FILE.format(file=file)
    return None


@dataclass(frozen=True)
class TuningRow:
    """One setting: what it is, what it says now, and whether it may be changed.

    `label` is never `None` -- the fallback to the key happens here, once, so
    the panel and the tests cannot spell it differently. `explain`, `type`,
    `min` and `max` stay `None` when the catalog is silent, and the panel reads
    that silence as "a text box and no prose" rather than filling it.
    """

    module_id: str
    module_name: str
    family: str
    file: str
    key: str
    label: str
    explain: str | None
    type: str | None
    min: int | None
    max: int | None
    default: str | None
    current: str | None
    installed: bool
    backend: Backend
    read_only_reason: str | None

    @property
    def editable(self) -> bool:
        return self.read_only_reason is None


def _is_conf_comment(line: str) -> bool:
    """`Config.cpp:310-314`: a trimmed line that is empty, `#` or `[` says nothing.

    A whole line and only a whole line. AzerothCore has no trailing-comment
    syntax: after the first `=`, everything to the end of the line is the value
    (see `conf_value`), so a `#` further along is part of the value and not a
    comment, however much it looks like one.

    The `[` arm changes no answer this module gives -- a `[` binds to the first
    token, so `[worldserver]` strips to `[worldserver` and can never equal a
    bare key -- and is kept because this function's claim is "what the core
    skips", not "what happens to matter here". `test_a_comment_and_a_section_
    header_are_both_lines_that_say_nothing` asserts it directly for that reason.
    """
    bare = line.strip()
    return bare == "" or bare[:1] in ("#", "[")


def conf_value(text: str, key: str) -> str | None:
    """The FIRST active setting of `key` in an AzerothCore `.conf`, or `None`.

    Read off the core's own parser (`src/common/Configuration/Config.cpp:305-331`,
    read 2026-09-13) rather than inherited, because inheriting it was wrong in
    two of three ways. `ParseFile`:

    1. TRIMS the whole line before deciding anything -- so an INDENTED
       assignment is live, and the column-0 rule this module started with
       reported `None` for one;
    2. skips the line only when it is then empty or starts with `#` or `[`;
    3. splits on the FIRST `=`, trims both halves, strips every `"` from the
       value -- and then `IsDuplicateOption` SKIPS every later copy of a key it
       has already stored, logging "Duplicate key name". **First wins.** This
       module started with last-wins, borrowed from `party.read_conf()`, which
       is Lua's rule (see `lua_value`) and not this file format's -- so the tab
       showed a value the server does not use, and the writer rewrote a line
       the server ignores.

    `apply._set_conf_key()` already had it right: its `subn(..., count=1)` is
    the first match.

    The `_is_conf_comment` call is the core's own first decision and is kept in
    its place, but it is REDUNDANT here and the mutation says so: the match
    below is exact, and a `#` or `[` binds to the first token, so `# K = 9`
    strips to `# K` and can never equal `K`. It earns its place by making the
    rule readable as `Config.cpp`'s rule rather than by excluding anything;
    `test_a_comment_and_a_section_header_are_both_lines_that_say_nothing`
    asserts it where it can actually fail.
    """
    for line in text.splitlines():
        if _is_conf_comment(line):
            continue
        head, sep, tail = line.partition("=")
        if sep and head.strip() == key:
            return tail.strip().strip('"')
    return None


def conf_keys(text: str) -> tuple[str, ...]:
    """Every key this conf actively assigns, in the file's order, once each.

    `conf_value()`'s rule applied to the whole file rather than to one name: a
    trimmed line that is empty, `#` or `[` says nothing (`Config.cpp:310-314`),
    and the key is everything before the first `=`. A commented-out default is
    the SHIPPED shape of most `.conf.dist` lines, so reading one as a key the
    file carries would be wrong about almost every file in the install.

    Its caller is `composegen.shadowed_by_env()`, which asks which of a file's
    keys the running containers override. Here and not there because this
    module owns how an AzerothCore conf is read, and a second parser in the
    compose generator is a second place for that rule to drift.
    """
    found: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip("\r").strip()
        if _is_conf_comment(line):
            continue
        cut = line.find("=")
        if cut <= 0:
            continue
        key = line[:cut].strip()
        if key and key not in found:
            found.append(key)
    return tuple(found)


def lua_value(text: str, key: str) -> str | None:
    """The LAST assignment of `key` in a deployed Lua script, or `None`.

    A different language, so a different rule, and the row's `backend` is what
    picks between them. Lua is last-assignment-wins -- DML's own reader takes
    the last for that reason (`crates/dml-wow/src/tuning.rs`, `lua_cfg_read`) --
    and its comments are `--`, not `#`.

    Column 0 here, and that IS `party.read_conf()`'s measured rule in the place
    it was measured: the shipped `mod_ale.conf.dist` carries a commented
    `ALE.Enabled = true` beside a compiled default of `false`, and a pattern
    that matched indented or commented lines read a file's own prose as its
    settings.
    """
    found: str | None = None
    for line in text.splitlines():
        head, sep, tail = line.partition("=")
        if sep and head.strip() == key and head[:1] not in ("#", " ", "\t", "-"):
            found = tail.strip().strip('"')
    return found


def value_in(text: str, key: str, backend: Backend) -> str | None:
    """Whichever of the two rules this backend's file format actually follows."""
    return conf_value(text, key) if backend == "conf" else lua_value(text, key)


NOT_UTF8 = "{file} is not UTF-8 text, so Yu'lon will not read or rewrite it: {why}"
"""A file this app cannot decode is a file it must not write.

`errors="replace"` reads a byte it does not understand as U+FFFD, and a write
back in UTF-8 then puts that replacement character on disk -- corrupting a line
this app was never asked to touch, in somebody's live configuration. Refusing
is the only answer that cannot lose a byte.
"""


def _read(path: Path) -> str | None:
    """The file's text, or `None` when it is not there, will not open, or is not UTF-8.

    `None` rather than `""`: an empty file has said every key is absent, and a
    missing one has said nothing at all. The row draws them the same way -- no
    current value -- but only one of them is worth a message about the install.

    A file that will not DECODE answers `None` too, for the sharper reason: a
    strict decode is the difference between "the file does not say" and "the
    file says U+FFFD", and `current` may never be the second.
    """
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError as exc:
        logger.debug(f"tuning: could not read {path}: {exc}")
        return None
    except UnicodeDecodeError as exc:
        logger.debug(f"tuning: {path} is not UTF-8: {exc}")
        return None


def rows_for(
    manifests: Iterable[Manifest],
    installed: Mapping[str, frozenset[str]],
    server_dir: Path,
) -> tuple[TuningRow, ...]:
    """Every setting this install can be tuned by, in the order the tab draws them.

    `manifests` is this game's catalog in the store's own order; `installed` is
    `apply.installed_clones()`'s answer, ids per family read from each family's
    OWN clone directory (T41). Rows come only from installed modules, and that
    is not a display filter: an uninstalled module has no deployed conf, so
    every value shown for it would be a value nothing reads.

    The order is family by family in `FAMILY_FILES` order, then the catalog's
    own order inside a family, then the manifest's own conf-file order, then its
    own key order. Four orderings and not one of them this module's invention --
    a catalog that reorders its keys reorders the cards, which is the only way
    the author gets to say what comes first.
    """
    rows: list[TuningRow] = []
    catalog = list(manifests)
    for kind in FAMILY_FILES:
        here = installed.get(kind, frozenset())
        for manifest in catalog:
            if manifest.type != kind or manifest.id not in here:
                continue
            for conf in manifest.conf:
                backend = backend_of(conf.file)
                # Read once per FILE, not once per key: a conf with a dozen keys
                # is one open, and the whole tab is one pass over the install.
                text = (
                    None
                    if _is_glob(conf.file) or backend == "other"
                    else _read(server_dir / conf.file)
                )
                for key in conf.keys:
                    reason = _read_only_reason(conf.file, backend, key.key)
                    rows.append(
                        TuningRow(
                            module_id=manifest.id,
                            module_name=manifest.name,
                            family=manifest.type,
                            file=conf.file,
                            key=key.key,
                            label=key.label or key.key,
                            explain=key.explain,
                            type=key.type,
                            min=key.min,
                            max=key.max,
                            default=key.default,
                            current=(None if text is None else value_in(text, key.key, backend)),
                            installed=True,
                            backend=backend,
                            read_only_reason=reason,
                        )
                    )
    return tuple(rows)


# -- the writer -------------------------------------------------------------


class TuningError(RuntimeError):
    """A refusal that names the key it is about, raised before anything is written."""


_ADDED_BY = "# Added by Yu'lon on {date} — this key was not in the file."
"""The comment a key the file does not carry is appended under.

Named and dated because the next person to read this file is entitled to know
which lines a program put there and when; `apply._set_conf_key()` appends such a
key silently today, and a conf full of unattributed lines is how a person stops
trusting their own configuration.
"""


def check(key: ConfKey | None, value: str) -> None:
    """Refuse a value that fails its own declared type, naming the key.

    A key with no `type` accepts anything -- it is a text box, and a text box
    that refused input would be a type check the catalog never declared. A
    `bool` takes the two spellings AzerothCore's own confs use. An `int` must
    parse, and must sit inside whichever bounds the catalog actually stated;
    where it stated none, none is invented.
    """
    if key is None or key.type is None:
        return
    if key.type == "int":
        try:
            number = int(value.strip())
        except ValueError:
            raise TuningError(f"{key.key}: `{value}` is not a whole number") from None
        if key.min is not None and number < key.min:
            raise TuningError(f"{key.key}: {number} is below the smallest allowed value {key.min}")
        if key.max is not None and number > key.max:
            raise TuningError(f"{key.key}: {number} is above the largest allowed value {key.max}")
        return
    if key.type == "bool" and value.strip().lower() not in _BOOL_WORDS:
        allowed = ", ".join(sorted(_BOOL_WORDS))
        raise TuningError(f"{key.key}: `{value}` is not an on/off value (use one of: {allowed})")


_BOOL_WORDS = frozenset({"0", "1", "true", "false"})
"""What a `bool` key accepts, and nothing wider.

`0`/`1` is what every AzerothCore module conf in this catalog is written with;
`true`/`false` is what the deployed Lua scripts use. Both spellings are let
through because both appear in files this tab shows, and a switch that wrote
`1` into a file whose other lines say `true` would be the tab inventing a
convention the module never had.
"""


def _newline_of(raw: str) -> str:
    """The line ending this file already uses, so a write does not convert it.

    First one wins and `\\r\\n` is checked before `\\n`, because a file written
    on Windows and edited here must come back out the way it went in: a conf
    silently converted to LF is a diff nobody asked for against an install a
    user may also edit with a Windows editor.
    """
    return "\r\n" if "\r\n" in raw else "\n"


PRIVATE_MODE = 0o600
"""What a copy is created with, before it takes its source's mode (`private_copy`)."""


def private_copy(src: Path, dst: Path) -> None:
    """Copy `src` to a NEW file `dst`, owner-only while the bytes land, then with `src`'s mode.

    Not `shutil.copy2`: that creates `dst` at the umask default (0644, say) and
    sets the source's mode only after the bytes are in, so a copy of a CMaNGOS
    conf -- the database password is in it -- is readable by every local
    account for that moment (T94 final review). Here the mode is asked for in
    the creating syscall (`O_EXCL`: never someone else's file), the bytes are
    copied, and `copystat` then gives `dst` exactly `src`'s mode and times, so a
    backup or a restore keeps the file's own mode (the gate: a conf a container
    user reads must not come back owner-only). A POSIX guarantee; the mode is a
    no-op on Windows (`conf._write`'s docstring). Raises `OSError`; a caller
    removes a half-written `dst`.
    """
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_MODE)
    with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
        shutil.copyfileobj(inp, out)
    shutil.copystat(src, dst)


def backup(path: Path, *, now: datetime | None = None, tag: str = "") -> Path:
    """Copy `path` beside itself, stamped, and return where it went.

    `tag`, when given, goes between the stamp and `.bak` (T94: a Reset to
    default's backups are `<name>.<stamp>.reset-<press>.bak`, so an Undo after
    a restart can tell them from a save's and find every file of one press).
    After the fixed-width stamp, so a name sort is still a time sort and
    `backups_of()` still lists them.

    The stamp comes from the clock rather than from a counter: two saves a
    minute apart are two backups, and a name that had to be searched for a free
    suffix would be a second thing to get wrong. Metadata is copied too
    (`private_copy`'s `copystat`), so the backup's own mtime says when the
    ORIGINAL was last touched and the name says when it was taken.

    The copy (`private_copy`: owner-only while it is written, then the file's
    own mode) goes to a `.yulon-tmp` sibling and is renamed onto the `.bak` name
    only once it is whole (T94 fix round 2): `copy2` straight onto the target
    leaves half a file when it dies (ENOSPC), and a half `.bak` is worse than
    none -- `backups_of()` lists it as the newest, so Revert would restore it,
    and a Reset to default's Undo reads a tagged one as a record. A failure
    removes the sibling and raises; nothing named `.bak` is left.
    """
    when = now or datetime.now()
    for _ in range(_BACKUP_TRIES):
        # Microseconds, in a FIXED-WIDTH field, so a name sort is a time sort
        # (`backups_of` depends on that) and two saves in the same second are
        # two files. A stamp to the second is not hypothetical: Save, read the
        # result, Save again is well inside one second, and the second backup
        # landed on top of the first -- destroying the only record of the file
        # the user actually wanted back.
        target = path.with_name(
            f"{path.name}.{when:%Y%m%d-%H%M%S-%f}{'.' + tag if tag else ''}.bak"
        )
        if not target.exists():
            tmp = target.with_name(f"{target.name}{TEMP_SUFFIX}")
            try:
                private_copy(path, tmp)
                os.replace(tmp, target)
            except BaseException:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(f"could not remove {tmp}: {exc}")
                raise
            return target
        # Not a counter suffix: `...-2.bak` sorts BEFORE `....bak`, which would
        # quietly make `backups_of()` report the wrong one as newest. The next
        # free microsecond keeps one field and one ordering.
        when += timedelta(microseconds=1)
    raise TuningError(f"{path.name}: could not find a free name for a backup")


_BACKUP_TRIES = 1000
TEMP_SUFFIX = ".yulon-tmp"
"""What a half-written conf is called while it is being written.

The write is a temp file in the SAME directory and an `os.replace` onto the
target, the pattern `state.py:126-131` and `steam.py::_write_compat` already
use. A plain `open(path, "w")` truncates first, so an ENOSPC or a SIGKILL
between the truncate and the last byte leaves the user's conf half a file --
with only the backup beside it and no sign of which one is which."""


def write(
    path: Path,
    edits: Mapping[str, str],
    *,
    spec: Mapping[str, ConfKey] | None = None,
    now: datetime | None = None,
) -> Path:
    """Set these keys in this conf, change nothing else, and return the backup's path.

    What survives a write, because a user's conf is theirs and not ours:
    comments, blank lines, the order of the keys, every key this call was not
    asked about, and the file's own line endings. Only the value to the right of
    a named key's `=` moves.

    A key the file does not contain is APPENDED under a dated comment naming the
    app, rather than skipped: the 73 keys with no default are exactly the keys a
    shipped `.conf.dist` may not mention, and dropping them silently is the
    defect this whole ticket exists for. A key the file names more than once has
    its LAST active assignment rewritten -- the one the server reads (see
    `conf_value`).

    Every value is checked against its own declared type FIRST, across all of
    them, so a card of six settings with one bad number writes none of the six
    and says which one. The backup is taken after the checks pass and before the
    first byte is written.
    """
    for key, value in edits.items():
        check(None if spec is None else spec.get(key), value)
    # `newline=""` on the way IN as well as out. The default translates every
    # "\r\n" to "\n" while reading, so a CRLF conf arrives looking like an LF
    # one, is detected as LF, and is written back converted -- a whole-file diff
    # over a one-key change, on exactly the installs (Windows) most likely to
    # have an editor open on the same file.
    #
    # And a STRICT decode: a byte this app cannot read is a byte it must not
    # rewrite. Refused here, before the backup, so a refusal leaves the
    # directory exactly as it found it.
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            raw = handle.read()
    except UnicodeDecodeError as exc:
        raise TuningError(NOT_UTF8.format(file=path.name, why=exc)) from None
    newline = _newline_of(raw)
    # Split on "\n" alone, so every line of a CRLF file keeps its own trailing
    # "\r" and `"\n".join(...)` puts the file back byte-for-byte. A file with
    # mixed endings keeps each line's own, rather than being normalised to
    # whichever one this function guessed.
    lines = raw.split("\n")
    carriage = "\r" if newline == "\r\n" else ""
    appended: list[str] = []
    for key, value in edits.items():
        index = _key_line(lines, key)
        if index is None:
            appended.append(f"{key} = {value}{carriage}")
            continue
        lines[index] = _rewrite(lines[index], key, value)
    made = backup(path, now=now)
    if appended:
        if lines and lines[-1].strip() == "":
            lines.pop()  # write under the trailing newline, not after a blank line
        stamp = (now or datetime.now()).strftime("%Y-%m-%d")
        lines.append(_ADDED_BY.format(date=stamp) + carriage)
        lines += appended
        lines.append("")
    _atomic_write(path, "\n".join(lines))
    return made


def _atomic_write(path: Path, text: str) -> None:
    """Put `text` on disk in one step, or leave what was there untouched.

    A sibling temp file and `os.replace`, which is atomic on both platforms this
    app runs on. The temp file is removed if anything goes wrong, so a failed
    save leaves neither a truncated conf nor a `.yulon-tmp` beside it for
    somebody to find later and wonder about.
    """
    temp = path.with_name(f"{path.name}{TEMP_SUFFIX}")
    try:
        with open(temp, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _key_line(lines: Sequence[str], key: str) -> int | None:
    """Where the assignment the SERVER reads is, or `None` if the file has none.

    The FIRST one, and `conf_value`'s rule exactly, so the line this rewrites is
    the line the tab read the current value from. `Config.cpp`'s
    `IsDuplicateOption` skips every later copy of a key, so rewriting the last
    one changed a line the server ignores: the value on screen would not move,
    and Save would look broken.

    The `_is_conf_comment` call is the core's own first decision and is kept in
    its place, but it is REDUNDANT here and the mutation says so: the match
    below is exact, and a `#` or `[` binds to the first token, so `# K = 9`
    strips to `# K` and can never equal `K`. It earns its place by making the
    rule readable as `Config.cpp`'s rule rather than by excluding anything;
    `test_a_comment_and_a_section_header_are_both_lines_that_say_nothing`
    asserts it where it can actually fail.
    """
    for index, line in enumerate(lines):
        if _is_conf_comment(line):
            continue
        head, sep, _ = line.partition("=")
        if sep and head.strip() == key:
            return index
    return None


def _rewrite(line: str, key: str, value: str) -> str:
    """Replace the VALUE on one assignment line and leave the rest of it alone.

    Kept byte-for-byte: the leading whitespace, the key as the file spells it,
    the separator exactly as written (`K    =    `), the quotes if the old value
    had them, and a trailing `\\r`. `split("\\n")` leaves a CRLF file's `\\r` on
    every line, and a rewrite that dropped it would convert exactly the lines it
    touched and leave the rest -- a file with mixed endings, worse than either.

    Replaced: everything after the separator, because that is what the server
    reads as the value. `Config.cpp:325` splits on the FIRST `=` and takes the
    rest of the line, so `K = old = fallback` has the value `old = fallback` and
    `K = old # keep this` has the value `old # keep this` -- AzerothCore has no
    trailing-comment syntax at all. Keeping a trailing `#...` "comment" would
    write the value `new # keep this` into a live conf, which is not what the
    person who moved the control asked for.
    """
    tail = "\r" if line.endswith("\r") else ""
    body = line[: len(line) - len(tail)]
    stripped = body.lstrip(" \t")
    lead = body[: len(body) - len(stripped)]
    after = stripped[len(key) :]
    equals = after.find("=")
    if equals < 0:  # `_key_line` only ever hands us a line that has one
        return f"{lead}{key} = {value}{tail}"
    end = equals + 1
    while end < len(after) and after[end] in " \t":
        end += 1
    separator, rest = after[:end], after[end:]
    quoted = '"' if rest.strip().startswith('"') else ""
    return f"{lead}{key}{separator}{quoted}{value}{quoted}{tail}"


def restore(from_backup: Path, target: Path) -> None:
    """Put a backup back, for the Revert press.

    A copy rather than a move: the backup is the only record of what the file
    said before, and a Revert that consumed it would leave a second Revert with
    nothing to restore.
    """
    shutil.copy2(from_backup, target)


def backups_of(path: Path) -> tuple[Path, ...]:
    """Every backup this module has taken of `path`, newest last.

    Sorted by NAME, which is the stamp, and not by mtime: `private_copy`'s
    `copystat` gives a backup the mtime of the file it copied, so two backups
    of an untouched file share an mtime and the newest of them is not the last
    one taken.
    """
    try:
        found = [p for p in path.parent.iterdir() if p.name.startswith(f"{path.name}.")]
    except OSError:
        return ()
    return tuple(sorted((p for p in found if p.suffix == ".bak"), key=lambda p: p.name))


# -- the raw editor's guard -------------------------------------------------


@dataclass(frozen=True)
class LintIssue:
    """One line of a raw edit that is not a setting, a comment, a blank or a header."""

    line: int
    """1-indexed, so it matches what the editor's own gutter says."""

    text: str


_SECTION = re.compile(r"^\[.*\]$")


def lint(text: str) -> tuple[LintIssue, ...]:
    """The "does this still look like a `.conf`?" verdict for a raw edit.

    DML's `launcher/src/lib/conf-lint.ts` rule, carried over so the two
    launchers refuse the same text: a line is fine if it is blank, a comment, an
    INI section header (`[worldserver]` opens every real AzerothCore conf), or
    an assignment with a non-empty key before its first `=`. Everything else is
    reported.

    It is a cheap pass and not a parser, and it never blocks: the panel shows
    the first offending line and puts Save behind one confirm. A guard that
    refused would be a guard between a user and their own file, over a rule this
    shallow.
    """
    issues: list[LintIssue] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.rstrip("\r").strip()
        if line == "" or line.startswith("#") or _SECTION.match(line):
            continue
        if line.find("=") <= 0:
            issues.append(LintIssue(number, line))
    return tuple(issues)


LINT_SENTENCE = (
    "Line {line} does not look like a setting: {text!r}. A .conf file holds `Key = Value` "
    "lines, comments starting with #, and [section] headers. Save it anyway?"
)


def lint_sentence(issues: Sequence[LintIssue]) -> str | None:
    """The FIRST offending line, in the sentence the confirm asks, or `None` when clean.

    The first and not all of them: one bad line is usually the edit that went
    wrong, and a dialog listing forty is a dialog nobody reads.
    """
    if not issues:
        return None
    first = issues[0]
    return LINT_SENTENCE.format(line=first.line, text=first.text)


# -- what a change costs ----------------------------------------------------

ApplyRule = Literal["rebuild", "recreate", "restart", "read-only"]

BOUND_INTO_THE_CONTAINERS: tuple[str, ...] = ("env/dist/etc/", "etc/")
"""The server-dir paths this app's compose binds into the running containers.

Read off `catalog/installers/wow-wotlk/native/base.yml.tmpl:160-165, 243-245`:
`./env/dist/etc` and `./env/dist/logs` go into `ac-worldserver`, `ac-db-import`
and `ac-authserver`, and `./modules` into the worldserver alone. Only the conf
directory matters here -- it is the one a tuning write lands in.

`etc/` is every CMaNGOS game's (T99): `catalog/installers/shared/cmangos/
base.yml.tmpl:81, 112` binds `./etc` into mangosd and realmd at the image's
compiled-in sysconfdir, so they read it off the user's disk at start and a
restart applies an edit. T94 priced it as a recreate and left this as its
follow-up. No AzerothCore file lives under a top-level `etc/`.
"""

APPLY_SENTENCES: dict[ApplyRule, str] = {
    "rebuild": (
        "The worldserver has to be COMPILED again before this takes effect — this setting "
        "lives in the module's own source tree, not in a file the server reads at start."
    ),
    "recreate": (
        "The containers have to be RECREATED before this takes effect, not just restarted: "
        "this file is not one the running containers read from your disk, so the copy they "
        "are using does not change when you save."
    ),
    "restart": (
        "The worldserver reads this file when it starts, so the change takes effect at the "
        "next restart. The file itself is on your disk and the server reads it from there."
    ),
    "read-only": "Yu'lon does not write this setting, so there is nothing to apply.",
}
"""What a person has to do for a change to reach the running server, per rule.

Sentences and not words, because the words are what DML got burned by:
`ModuleFiles.svelte:124-128` says promising the fast world-only restart over a
file that needs a recreate "would be a promise we cannot keep."
"""


def file_rule(file: str) -> ApplyRule:
    """The same clauses as `apply_rule`, for a RAW file that has no row behind it.

    The raw editor opens a file, not a setting, so there is nothing to ask for
    a `read_only_reason` -- but the sentence under the editor has to be the same
    sentence the card above it shows, or the tab would price the same change two
    ways depending on which half of it the user pressed.
    """
    if _read_only_reason(file, backend_of(file)) is not None:
        return "read-only"
    if any(file.startswith(prefix) for prefix in BOUND_INTO_THE_CONTAINERS):
        return "restart"
    return "recreate"


def apply_rule(row: TuningRow, *, in_clone: bool = False) -> ApplyRule:
    """What has to happen for a change to this row to reach the running server.

    Computed from the row, never typed into the view, so the chip on a card and
    the sentence under the raw editor cannot disagree — and so a fifth answer
    cannot be added by writing one in a layout.

    The three clauses, in the order they decide:

    1. a setting this app does not write at all (a deployed `.lua`, a database
       table, a glob) is `read-only`: there is nothing to apply;
    2. a setting in the module's own SOURCE tree needs a `rebuild` — the running
       worldserver has the old value compiled into it;
    3. a conf file inside a directory the compose binds is read off the user's
       own disk at world start, so a `restart` is enough. A conf file OUTSIDE
       every bind is a copy baked into the image: saving it changes the disk and
       not the server, and only a `recreate` picks the new one up.
    """
    if row.read_only_reason is not None:
        return "read-only"
    if in_clone:
        return "rebuild"
    return file_rule(row.file)


def apply_sentence(rule: ApplyRule) -> str:
    return APPLY_SENTENCES[rule]


_JOBS: tuple[ApplyRule, ...] = ("rebuild", "recreate", "restart")
"""The three rules that are JOBS, most expensive first. `read-only` is not one."""


def owed(rules: Iterable[ApplyRule]) -> tuple[ApplyRule, ...]:
    """EVERY distinct job a card's changes owe, most expensive first.

    Not the most expensive one. A card whose rows need a rebuild AND a recreate
    owes both: a rebuild compiles a new image, and a container started from the
    old one goes on running until something recreates it. Reporting only the
    rebuild is a cheaper answer than the truth, which is the dangerous
    direction -- the user does the one job they were told about, sees no change,
    and concludes the setting does not work.

    This replaced a `worst()` that ranked the four and returned one. It read
    correctly for every single-rule card, which is every card the shipped
    catalog can produce today, and would have been wrong the first time one
    module's keys spanned two costs.
    """
    seen = set(rules)
    return tuple(job for job in _JOBS if job in seen)


def owed_sentence(rules: Sequence[ApplyRule]) -> str:
    """What a card says it costs: one sentence per job, or the read-only one."""
    if not rules:
        return APPLY_SENTENCES["read-only"]
    return " ".join(APPLY_SENTENCES[rule] for rule in rules)
