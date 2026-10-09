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

import bisect
import itertools
import math
import os
import re
import shutil
import stat
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from yulon import links
from yulon.log import get_logger
from yulon.manifest import ConfKey, Manifest
from yulon.manifest_store import FAMILY_FILES
from yulon.said import SaidByYulon

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


MODULE_CONF_DIR = "env/dist/etc/modules"
"""Where an AzerothCore install keeps its modules' `.conf` files, under the server folder."""


def module_conf_files(server_dir: Path) -> tuple[str, ...]:
    """Every `.conf` in the server's modules folder, by name, relative to `server_dir`.

    The raw editor's list used to come from `rows_for()` alone, so a module the
    catalog does not describe -- or whose only declared key is a wildcard such as
    `AutoBalance.Enable.*` -- had no button (T569: 16 of a player's 52). This
    reads the folder itself. Plain files ending in `.conf` only: a `.conf.dist`
    default, a sub-folder and a dangling link are not something to edit, and a
    folder that is missing or cannot be listed answers nothing rather than
    failing the tab. Sorted by lower-cased name so the order does not depend on
    the file system's.
    """
    try:
        with os.scandir(server_dir / MODULE_CONF_DIR) as entries:
            names = [
                entry.name
                for entry in entries
                if entry.name.lower().endswith(CONF_SUFFIX) and entry.is_file()
            ]
    except OSError as exc:
        logger.debug(f"tuning: could not list {server_dir / MODULE_CONF_DIR}: {exc}")
        return ()
    names.sort(key=lambda name: (name.lower(), name))
    return tuple(f"{MODULE_CONF_DIR}/{name}" for name in names)


# -- the writer -------------------------------------------------------------


class TuningError(RuntimeError):
    """A refusal that names the key it is about, raised before anything is written."""


class RateRefused(TuningError, SaidByYulon):
    """A `float` value refused in a sentence for the player, naming the row by its label.

    `SaidByYulon` so the player-lines guard (`test_player_lines_name_no_commands`)
    reads every one of these sentences in the source (live test of T302, 2026-10-05:
    the line read "Rate.XP.Kill: `0.00001` ...", a key and backticks).
    """


_ADDED_BY = "# Added by Yu'lon on {date} — this key was not in the file."
"""The comment a key the file does not carry is appended under.

Named and dated because the next person to read this file is entitled to know
which lines a program put there and when; `apply._set_conf_key()` appends such a
key silently today, and a conf full of unattributed lines is how a person stops
trusting their own configuration.
"""


WHOLE_NUMBER = re.compile(r"-?[0-9]+")
"""How an `int` key's value may be spelled: ASCII digits and an optional leading minus.

Narrower than Python's `int()` on purpose, which also reads other scripts' digits
(`int("١٢") == 12`), underscores (`int("1_0") == 10`), a plus and surrounding
spaces. The value is written as typed, and the cores read it in C++:
AzerothCore's `Acore::StringTo<T>` (`src/common/Configuration/Config.cpp:584`,
`src/common/Utilities/StringConvert.h:70` at its pin) runs `std::from_chars` over
the whole value and falls back to the default on anything but this spelling;
mangos-tbc's `GetIntDefault` (`src/shared/Config/Config.cpp:127-131`) is
`std::stoi`, which reads `1_000` as 1 and `0x10` as 0 and throws -- at world
start -- on a value with no digit in front. Matched against the value itself,
not a stripped copy, because a space is part of what gets written.
"""

INT32_SMALLEST = -(2**31)
INT32_LARGEST = 2**31 - 1
"""The range of the C++ `int` an `int` key is read into.

`GetIntDefault` returns `int32` in mangos-tbc (`std::stoi`, which throws
`out_of_range` at world start past it), Tortoise (`atoi`) and Centurion. An
AzerothCore module may read a key as `uint32` instead (`mod-ah-bot`'s GUID), and
then 2147483648 and up is refused although that module could read it: no real
GUID or count lives there, while accepting it for an `int32` reader is a value
the server never sees. A key the module reads as `uint32` says so with `unsigned`.
"""


UINT32_LARGEST = 2**32 - 1
"""The top of a `uint32` key's range (T370): `ConfKey.unsigned` / `Prompt.unsigned` says
the module reads the value with `GetOption<uint32>`, so it starts at 0 and ends here."""


def int_range(unsigned: bool) -> tuple[int, int]:
    """The (smallest, largest) a whole-number key or answer may hold: one rule for both callers."""
    return (0, UINT32_LARGEST) if unsigned else (INT32_SMALLEST, INT32_LARGEST)


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
        if WHOLE_NUMBER.fullmatch(value) is None:
            spaces = ", with no spaces" if any(ch.isspace() for ch in value) else ""
            example = "like 12" if key.unsigned else "like 12 or -5"
            raise TuningError(
                f"{key.key}: '{value}' is not a whole number; "
                f"type it with the digits 0 to 9 only{spaces}, {example}"
            )
        number = int(value)
        smallest, largest = int_range(key.unsigned)
        if not smallest <= number <= largest:
            raise TuningError(
                f"{key.key}: {value} is not a number the server can hold; "
                f"use a number from {smallest} to {largest}"
            )
        if key.min is not None and number < key.min:
            raise TuningError(f"{key.key}: {number} is below the smallest allowed value {key.min}")
        if key.max is not None and number > key.max:
            raise TuningError(f"{key.key}: {number} is above the largest allowed value {key.max}")
        return
    if key.type == "float":
        _check_decimal(key, value)
        return
    if key.type == "bool" and value.strip().lower() not in _BOOL_WORDS:
        allowed = ", ".join(sorted(_BOOL_WORDS))
        raise TuningError(f"{key.key}: `{value}` is not an on/off value (use one of: {allowed})")


DECIMAL = re.compile(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
"""How a `float` key's value may be spelled: digits, at most one point, an optional minus.

Narrower than Python's `float()` on purpose (T302). The cores parse a rate in C:
mangos-tbc and mangos-classic with `std::stof` (`src/shared/Config/Config.cpp:134-139`
at their pins), which throws -- at world start -- on a value that does not begin
with a number, and Tortoise with `atof` (`src/shared/Config/Config.cpp:261-265`).
Both stop at the first character they do not expect, so `float("1_0")` is 10 to
this app and 1 to the server, and `1,5` is 1. `inf` and `nan` are numbers to
Python and not rates. `[0-9]` and not `\\d`, which in a Python pattern also
matches other scripts' digits: `float("١.٥")` is 1.5, and the server reads no
number at all (Codex review, 2026-10-05). A value the two sides would read
differently is refused rather than written.
"""


DECIMAL_PLACES = 4
"""The most digits a `float` key may have after its point (cold review, 2026-10-05).

`std::stof` throws `out_of_range` -- at world start, uncaught -- on a value under a
C `float`'s smallest normal number (about 1.2e-38), and `0.` followed by forty
zeros and a 1 is still a plain decimal. Four places keeps the smallest non-zero
value at 0.0001, which every core reads, and is finer than any rate needs.
"""

FLOAT_LARGEST = 3.4028234663852886e38
"""The largest number a C `float` holds (`FLT_MAX`): the cores read a rate into one,
and `std::stof` throws `out_of_range` above it while Python's `float()` does not."""


def decimal_fault(text: str) -> str:
    """Why this text is not a decimal the cores read as typed, or `""` (T395).

    `"spelling"` (not `DECIMAL`), `"decimals"` (past `DECIMAL_PLACES`) or `"large"`
    (past a C `float`). The one rule behind a `float` Tuning key AND a `float`
    module question, each wording the refusal its own way.
    """
    if DECIMAL.fullmatch(text) is None:
        return "spelling"
    _, point, places = text.partition(".")
    if point and len(places) > DECIMAL_PLACES:
        return "decimals"
    number = float(text)
    # Digits only, and still past what the server's float holds: `float()` says
    # `inf` or a large double, and the core's parser says out of range (Codex
    # review and cold review, 2026-10-05).
    if not math.isfinite(number) or abs(number) > FLOAT_LARGEST:
        return "large"
    return ""


def _check_decimal(key: ConfKey, value: str) -> None:
    """A `float` key's rule: a plain decimal, inside whichever bounds the key states.

    Each refusal is one plain sentence naming the row as the card does (its
    label; the key only where a declaration has none).
    """
    row = key.label or key.key
    text = value.strip()
    if not text:
        raise RateRefused(f"{row} is empty. Write a number like 1, 2 or 1.5.")
    fault = decimal_fault(text)
    if fault == "spelling":
        raise RateRefused(f"{row}: {text} is not a number. Write it like 1, 2 or 1.5.")
    if fault == "decimals":
        raise RateRefused(
            f"{row}: {text} has too many decimals; use at most "
            f"{DECIMAL_PLACES} digits after the point, like 0.0001."
        )
    if fault == "large":
        raise RateRefused(f"{row}: {text} is too large to be a number.")
    number = float(text)
    if key.min is not None and number < key.min:
        raise RateRefused(f"{row}: {text} is below the smallest allowed value {key.min}.")
    if key.max is not None and number > key.max:
        raise RateRefused(f"{row}: {text} is above the largest allowed value {key.max}.")


def core_bool(value: str) -> bool | None:
    """What AzerothCore's `GetOption<bool>` makes of `value`, or `None`.

    `StringTo<bool>` non-strict (`StringConvert.h:94-122` at 7f12e89e): `1`,
    `y`, `on`, `yes`, `true` are on and `0`, `n`, `off`, `no`, `false` are off,
    letters in any case. `None` is a bad value, for which the core logs and
    uses the option's compiled default.
    """
    word = value.strip()
    if word == "1" or word.lower() in ("y", "on", "yes", "true"):
        return True
    if word == "0" or word.lower() in ("n", "off", "no", "false"):
        return False
    return None


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


def _disk_ignores_case() -> bool:
    """Whether this platform's usual disk treats `Foo.conf` and `foo.conf` as one file.

    Windows (NTFS) and macOS (APFS, by default) do; Linux does not. A seam for
    tests, which cannot run another platform's disk.
    """
    return sys.platform in ("win32", "darwin")


def file_key(name: str) -> str:
    """`name` spelled as this platform's disk compares file names (T573 item 3).

    `os.path.normcase` is the identity on POSIX, so on a case-insensitive macOS
    volume it called the server's `playerbots.conf` and a folder file
    `Playerbots.conf` two files: an editable second button beside the read-only
    one, and a Save that wrote the server's own file. Compare names by this key.
    """
    return name.replace("\\", "/").casefold() if _disk_ignores_case() else name


def is_one_of(name: str, names: Iterable[str], root: Path | None = None) -> bool:
    """Whether `name` is one of `names`, by `file_key`.

    `file_key` assumes the platform's usual disk, and a Mac volume can be
    case-sensitive (Codex review). So when `root` (the server folder) is given and
    two spellings differ only in case, both files existing there and not being the
    same file (`os.path.samefile`) settles it: they are two files. A spelling that
    does not exist on disk falls back to the key, the safe side (read-only).
    """
    key = file_key(name)
    for other in names:
        if other == name:
            return True
        if file_key(other) != key:
            continue
        if root is not None:
            try:
                if not os.path.samefile(root / name, root / other):
                    continue
            except OSError:
                pass
        return True
    return False


MAX_EDIT_BYTES = 1024 * 1024
"""The largest conf the raw editor opens: 1 MB (T573 item 4).

The shipped `worldserver.conf` is about 150 KB. The editor reads on the window's
own thread, so a file of hundreds of megabytes (a log renamed to `.conf`, say)
would freeze the app and fill the editor; past this size the file opens empty and
read-only with `TOO_BIG` instead.
"""

TOO_BIG = (
    "{file} is too big to edit here: this editor opens files up to {limit} MB. "
    "Open it in a text editor, or keep it under that size."
)


BOM = "\ufeff"
_TERMINATOR = re.compile(r"\r\n|\n|\r")


def editor_view(raw: str) -> str:
    """The text the raw editor is given for a file whose exact text is `raw` (T573).

    Without a leading byte-order mark, and with every line ending, `\\r\\n`,
    `\\n` and a lone `\\r` alike, as the `\\n` the editor hands back anyway.
    `save_text()` puts the originals back.
    """
    return _TERMINATOR.sub("\n", raw.removeprefix(BOM))


def save_text(raw: str, edited: str) -> str:
    """The file's new text after an edit in the raw editor, every untouched line as it was.

    `raw` is the file as it was read (`newline=""`, so nothing is translated) and
    `edited` is what the editor now holds. The editor cannot carry a byte-order
    mark or tell `\\r\\n` from `\\n` from a lone `\\r`, so writing its text back
    converted the whole file (T573 item 2). Here the two are lined up by line
    (`_matched_runs`), and a line the player did not change is written with its own
    bytes: its text and its own ending. A line they changed or added takes the ending
    of the line it replaced, or else of the one above it, or else the file's usual one
    (`_newline_of`). A leading BOM is kept.
    """
    bom = BOM if raw.startswith(BOM) else ""
    body = raw.removeprefix(BOM)
    # The file's usual ending (`_newline_of`), or a lone CR if every line ends in one.
    default = "\r" if "\r" in body and "\n" not in body else _newline_of(body)
    old_lines, old_ends = _lines_of(body)
    # The old last line has no ending: one that takes its place takes the usual one.
    old_ends.append(default)
    new_lines = (_TERMINATOR.sub("\n", edited) if "\r" in edited else edited).split("\n")
    ends = [default] * len(new_lines)
    # Between two matched runs (or a file's start or end) the old and new lines left over
    # were replaced: the first new one takes the first old one's ending, and so on; a new
    # line with no old one left takes the ending of the line above it.
    was = now = 0
    for i, j, size in [*_matched_runs(old_lines, new_lines), (len(old_lines), len(new_lines), 0)]:
        if j > now:
            paired = min(i - was, j - now)
            if paired:
                ends[now : now + paired] = old_ends[was : was + paired]
            if now + paired < j and now + paired > 0:
                ends[now + paired : j] = [ends[now + paired - 1]] * (j - now - paired)
        if size == 1:
            ends[j] = old_ends[i]
        else:
            ends[j : j + size] = old_ends[i : i + size]
        was, now = i + size, j + size
    ends[-1] = ""
    # A lone-CR line followed by an empty line that ends in a bare LF would read back as ONE
    # CRLF: the blank line gone. Such an empty line takes a lone CR as well.
    if body.count("\r") > body.count("\r\n"):
        for k in range(len(new_lines) - 1):
            if ends[k] == "\r" and new_lines[k + 1] == "" and ends[k + 1] == "\n":
                ends[k + 1] = "\r"
    text = [""] * (2 * len(new_lines))
    text[0::2] = new_lines
    text[1::2] = ends
    return bom + "".join(text)


def _lines_of(body: str) -> tuple[list[str], list[str]]:
    """`body`'s lines and the ending of each but the last, which has none."""
    if "\r" not in body:
        lines = body.split("\n")
        return lines, ["\n"] * (len(lines) - 1)
    if body.count("\r") == body.count("\n") == body.count("\r\n"):
        lines = body.split("\r\n")
        return lines, ["\r\n"] * (len(lines) - 1)
    parts = _SPLIT_LINES.split(body)
    return parts[0::2], parts[1::2]


_SPLIT_LINES = re.compile(r"(\r\n|\n|\r)")
"""`_TERMINATOR` kept in the split: a file's lines and their endings, taken turn about."""


def _matched_runs(old: Sequence[str], new: Sequence[str]) -> list[tuple[int, int, int]]:
    """Which old lines the unchanged new lines are: (old index, new index, how many) runs.

    The runs rise on both sides and only equal lines are ever in one. A line diff of a
    whole big file took seconds on the window's thread (7.7 s for 2 MB of alike
    sections), and matching the lines by their place alone gave hundreds of untouched
    lines a neighbour's ending once an edit added or removed a line. So: the lines both
    sides start and end with match first. What is left, if it has at most
    `MAX_DIFF_LINES` lines a side, is lined up exactly (`_line_up`). A bigger gap is
    cut, patience style, at its anchors: the longest run, in the same order on both sides,
    of lines whose text occurs exactly once in the old part and once in the new (n log n);
    each piece between two anchors is lined up the same way again. A big gap with no
    anchor (every line in it occurs twice or more, or on one side only) goes to
    `_in_order`. Exact line-ups cost a cell per pair of lines, and all of them together,
    `_in_order`'s included, stop at `_EXACT_CELLS` a save.
    """
    runs: list[tuple[int, int, int]] = []
    # Each search for anchors costs a pass over its gap. A file built to make every pass
    # find one anchor only would make that quadratic, so past a few passes over the
    # file's size no more anchors are looked for.
    passes = 8 * (len(old) + len(new)) + 10_000
    cells = [_EXACT_CELLS]
    todo = [(0, len(old), 0, len(new))]
    while todo:
        a1, a2, b1, b2 = todo.pop()
        same = _same_ahead(old, a1, new, b1, min(a2 - a1, b2 - b1))
        if same:
            runs.append((a1, b1, same))
            a1, b1 = a1 + same, b1 + same
        same = _same_behind(old, a2, new, b2, min(a2 - a1, b2 - b1))
        if same:
            a2, b2 = a2 - same, b2 - same
            runs.append((a2, b2, same))
        if a1 == a2 or b1 == b2:
            continue
        area = (a2 - a1) * (b2 - b1)
        if a2 - a1 <= MAX_DIFF_LINES and b2 - b1 <= MAX_DIFF_LINES and area <= cells[0]:
            cells[0] -= area
            texts = (set(old[a1:a2]), set(new[b1:b2]))
            found = _line_up(old[a1:a2], new[b1:b2], texts)
            runs.extend((a1 + i, b1 + j, 1) for i, j in found)
            continue
        passes -= (a2 - a1) + (b2 - b1)
        anchors = _anchors(old, a1, a2, new, b1, b2) if passes > 0 else []
        if not anchors:
            runs.extend(_in_order(old, a1, a2, new, b1, b2, cells))
            continue
        for i, j in anchors:
            runs.append((i, j, 1))
            todo.append((a1, i, b1, j))
            a1, b1 = i + 1, j + 1
        todo.append((a1, a2, b1, b2))
    runs.sort()
    return runs


def _same_ahead(old: Sequence[str], i: int, new: Sequence[str], j: int, most: int) -> int:
    """How many lines old[i:] and new[j:] start with alike, at most `most`.

    Compared a slice at a time, the slices doubling while they agree and halving once one
    does not, so a long run costs a few comparisons and not one Python step a line.
    """
    same, step = 0, 1
    while same < most:
        size = min(step, most - same)
        if old[i + same : i + same + size] == new[j + same : j + same + size]:
            same += size
            step *= 2
        elif size == 1:
            break
        else:
            step = size // 2
    return same


def _same_behind(old: Sequence[str], i: int, new: Sequence[str], j: int, most: int) -> int:
    """How many lines old[:i] and new[:j] end with alike, at most `most` (`_same_ahead`)."""
    same, step = 0, 1
    while same < most:
        size = min(step, most - same)
        if old[i - same - size : i - same] == new[j - same - size : j - same]:
            same += size
            step *= 2
        elif size == 1:
            break
        else:
            step = size // 2
    return same


def _anchors(
    old: Sequence[str], a1: int, a2: int, new: Sequence[str], b1: int, b2: int
) -> list[tuple[int, int]]:
    """The longest same-order run of lines that occur once in old[a1:a2] and once in new[b1:b2]."""
    in_old, in_new = Counter(old[a1:a2]), Counter(new[b1:b2])
    unique = {text for text, count in in_old.items() if count == 1 and in_new[text] == 1}
    if not unique:
        return []
    once = {
        old[i]: i for i in itertools.compress(range(a1, a2), map(unique.__contains__, old[a1:a2]))
    }
    found = [
        (j, once[new[j]])
        for j in itertools.compress(range(b1, b2), map(unique.__contains__, new[b1:b2]))
    ]
    # Longest increasing run of old indexes, in new order (patience sorting).
    tops: list[int] = []
    top_at: list[int] = []
    back = [-1] * len(found)
    for k, (_, i) in enumerate(found):
        pile = bisect.bisect_left(tops, i)
        if pile == len(tops):
            tops.append(i)
            top_at.append(k)
        else:
            tops[pile] = i
            top_at[pile] = k
        back[k] = top_at[pile - 1] if pile else -1
    run: list[tuple[int, int]] = []
    k = top_at[-1] if top_at else -1
    while k >= 0:
        run.append((found[k][1], found[k][0]))
        k = back[k]
    run.reverse()
    return run


def _in_order(
    old: Sequence[str],
    a1: int,
    a2: int,
    new: Sequence[str],
    b1: int,
    b2: int,
    cells: list[int],
) -> list[tuple[int, int, int]]:
    """Equal lines of old[a1:a2] and new[b1:b2] matched in order, as `_matched_runs` runs."""
    texts = (set(old[a1:a2]), set(new[b1:b2]))
    # After each side's lines, a few no line equals (a line holds no newline), so `_walk`
    # can look a few lines past either end without a check of its own.
    mine, yours = [*old[a1:a2], *_PAST_OLD], [*new[b1:b2], *_PAST_NEW]
    runs = _walk(mine, a2 - a1, yours, b2 - b1, texts, cells)
    return [(a1 + p, b1 + q, size) for p, q, size in runs]


def _walk(
    old: list[str],
    n: int,
    new: list[str],
    m: int,
    texts: tuple[set[str], set[str]],
    cells: list[int],
) -> list[tuple[int, int, int]]:
    """Equal lines of old[:n] and new[:m] matched in order; `texts` is each side's texts.

    Both lists go on past `n` and `m` with lines no line equals (`_in_order`). Where the two
    differ, the next `_WINDOW` lines of each side are lined up exactly (`_line_up`)
    and the matches in the window's first half are kept: a few lines changed, added or
    deleted among alike ones resync at once. A window with no line in common means a big
    block was added or deleted: the side whose line turns up again sooner on the other
    side skips ahead to it, and a line that turns up on neither side again was replaced,
    so both move on. Each window is charged to `cells`; once they are spent, the rest
    goes to `_walk_on`, which takes linear time whatever the lines are.
    """
    runs: list[tuple[int, int, int]] = []
    where_old: dict[str, list[int]] | None = None
    where_new: dict[str, list[int]] = {}
    # How many lines whose text the other side has each side has up to a place.
    kept_old: list[int] = []
    kept_new: list[int] = []
    i = j = 0
    while i < n and j < m:
        if old[i] == new[j]:
            same = _same_from(old, i, n, new, j, m)
            runs.append((i, j, same))
            i, j = i + same, j + same
            continue
        tall, wide = min(_WINDOW, n - i), min(_WINDOW, m - j)
        if tall * wide + tall + wide > cells[0]:
            cells[0] = 0
            runs.extend(_walk_on(old, i, n, new, j, m))
            break
        cells[0] -= tall * wide + tall + wide
        here, there = old[i : i + tall], new[j : j + wide]
        whole = i + _WINDOW >= n and j + _WINDOW >= m
        found: list[tuple[int, int]] = []
        if not set(here).isdisjoint(there):
            if not kept_old:
                kept_old = [0, *itertools.accumulate(map(texts[1].__contains__, old[:n]))]
                kept_new = [0, *itertools.accumulate(map(texts[0].__contains__, new[:m]))]
            surplus = kept_old[n] - kept_old[i] - kept_new[m] + kept_new[j]
            found = _line_up(here, there, texts, whole, surplus)
        if found:
            if not whole:
                half = _WINDOW // 2
                found = [p for p in found if p[0] < half and p[1] < half] or found[:1]
            runs.extend((i + di, j + dj, 1) for di, dj in found)
            i, j = i + found[-1][0] + 1, j + found[-1][1] + 1
            continue
        if where_old is None:
            where_old = {}
            for k in range(n):
                where_old.setdefault(old[k], []).append(k)
            for k in range(m):
                where_new.setdefault(new[k], []).append(k)
        in_old = _next_at(where_old.get(new[j], []), i)
        in_new = _next_at(where_new.get(old[i], []), j)
        if in_old is not None and (in_new is None or in_old - i <= in_new - j):
            i = in_old
        elif in_new is not None:
            j = in_new
        else:
            i, j = i + 1, j + 1
    return runs


def _same_from(old: list[str], i: int, n: int, new: list[str], j: int, m: int) -> int:
    """How many lines from old[i] == new[j] on are alike: a step a line for a short run."""
    same = 1
    while same < 8 and old[i + same] == new[j + same]:
        same += 1
    return _same_ahead(old, i, new, j, min(n - i, m - j)) if same == 8 else same


def _next_at(places: list[int], start: int) -> int | None:
    """The first of `places` (rising) at or after `start`, if any."""
    k = bisect.bisect_left(places, start)
    return places[k] if k < len(places) else None


def _walk_on(
    old: list[str], i: int, n: int, new: list[str], j: int, m: int
) -> list[tuple[int, int, int]]:
    """Equal lines of old[i:n] and new[j:m] in order, in linear time: `_walk` once spent.

    Where the two differ, the nearer of old's next line equal to new[j] and new's next
    line equal to old[i], within `_LOOK` lines, is skipped to; else both lines count as
    replaced. Every step moves on at least one line and looks at no more than
    2 * `_LOOK` + 1 of them.
    """
    runs: list[tuple[int, int, int]] = []
    while i < n and j < m:
        mine, yours = old[i], new[j]
        if mine == yours:
            same = 1 if old[i + 1] != new[j + 1] else _same_from(old, i, n, new, j, m)
            runs.append((i, j, same))
            i, j = i + same, j + same
            continue
        for skip in _SKIPS:
            if old[i + skip] == yours:
                i += skip
                break
            if new[j + skip] == mine:
                j += skip
                break
        else:
            i, j = i + 1, j + 1
    return runs


def _may_change(lose: list[int]) -> list[bool]:
    """Which lines `_line_up` may take for a changed line (`lose` is each one's cost).

    Lines whose text the other side has nowhere, in a run of at most `_CHANGE_RUN` of
    them; a longer run reads as lines added or deleted.
    """
    may = [False] * len(lose)
    start = 0
    for end, left_out in enumerate([*lose, 1]):
        if left_out:
            if end - start <= _CHANGE_RUN:
                may[start:end] = [True] * (end - start)
            start = end + 1
    return may


def _line_up(
    old: Sequence[str],
    new: Sequence[str],
    texts: tuple[set[str], set[str]],
    closed: bool = True,
    surplus: int = 0,
) -> list[tuple[int, int]]:
    """The equal lines of two short line lists, as (old, new) index pairs, both rising.

    The cheapest line-up wins. Leaving out a line costs `_LEFT_OUT`, unless the other side
    has its text nowhere (`texts` holds each side's texts): such a line can never match,
    so it costs nothing. Pairing an old line with a new one where one of them is such a
    line (a changed line) costs `_CHANGED`, less than leaving the other out, so a changed
    line among twins (`E = 1`, `E = 1` to `C`, `E = 1`) keeps the twin after it in place.
    Not `closed`, the two are the first lines of longer lists (`_walk`'s window): the
    lines past one side's end may still match the other's, so the line-up runs to the
    cheapest place on the far edge, of equal ones the one with the smaller offset
    between the sides. Counting matches alone let a block of added lines (fresh ones,
    or three `E = 1` lines among `#`, blank, `E = 1` sections) pull everything after it
    onto a twin a section on, since the true line-up's last lines fall past the
    window's edge. Where the line-up ends is also charged `_BEHIND` a line for how far
    it leaves the two sides from evening out: `surplus` is how many more lines whose
    text the other side has the old side has than the new from the window on, which
    edits further on must still leave out. Among twins, a fresh line added and an old
    line changed into it read the same in a window; the line count of the whole tells
    them apart.
    """
    ours, theirs = texts
    lose_old = [_LEFT_OUT if text in theirs else 0 for text in old]
    lose_new = [_LEFT_OUT if text in ours else 0 for text in new]
    cost = [[0, *itertools.accumulate(lose_new)]]
    cost += [[0] * (len(new) + 1) for _ in old]
    may_old, may_new = _may_change(lose_old), _may_change(lose_new)
    columns = list(zip(range(1, len(new) + 1), new, lose_new, may_new, strict=True))
    for x, (text, lose, may) in enumerate(zip(old, lose_old, may_old, strict=True), 1):
        above, row = cost[x - 1], cost[x]
        left = row[0] = above[0] + lose
        for y, other, extra, can in columns:
            if text == other:
                left = above[y - 1]
            else:
                left += extra
                if above[y] + lose < left:
                    left = above[y] + lose
                if (may or can) and above[y - 1] + _CHANGED < left:
                    left = above[y - 1] + _CHANGED
            row[y] = left
    x, y = len(old), len(new)
    if not closed:
        # The cheapest place on the far edge, of equal ones the nearest the diagonal.
        kept_old = [0, *itertools.accumulate(map(bool, lose_old))]
        kept_new = [0, *itertools.accumulate(map(bool, lose_new))]

        def price(x: int, y: int) -> tuple[int, int, int, int]:
            behind = abs(surplus - kept_old[x] + kept_new[y])
            return (cost[x][y] + _BEHIND * behind, abs(x - y), x, y)

        far = [price(len(old), y) for y in range(len(new) + 1)]
        far += [price(x, len(new)) for x in range(len(old))]
        x, y = min(far)[2:]
    found: list[tuple[int, int]] = []
    while x and y:
        here = cost[x][y]
        if old[x - 1] == new[y - 1]:
            found.append((x - 1, y - 1))
            x, y = x - 1, y - 1
        elif (may_old[x - 1] or may_new[y - 1]) and cost[x - 1][y - 1] + _CHANGED == here:
            x, y = x - 1, y - 1
        elif cost[x - 1][y] + lose_old[x - 1] == here:
            x -= 1
        else:
            y -= 1
    found.reverse()
    return found


_WINDOW = 32
"""How many lines a side `_walk` lines up exactly where the two texts differ."""

_LEFT_OUT = 5
_CHANGED = 2
_BEHIND = 3
_CHANGE_RUN = 6
"""What `_line_up` charges for a line left out, for an old line paired with a new one where
either text is not on the other side at all (a changed line), and a line for each line a
window's line-up leaves the two sides from evening out; and the longest run of such lines
it takes for changed lines. A change costs less than leaving its old line out, so changed
lines among twins stay paired with the lines they replaced; but not nothing, or added
lines that the old text has nowhere could be paired with old lines just as cheaply as
added, moving every line after them a section on (two-thirds of 600 lines in a measured
case). A pasted block longer than `_CHANGE_RUN` is added lines: let it absorb changes made
further down and the lines between moved a section on (397 of 600)."""

_LOOK = 4
"""How many lines a side `_walk_on` looks ahead once the exact line-ups are spent."""

_SKIPS = range(1, _LOOK + 1)
_PAST_OLD = ["\n<"] * _LOOK
_PAST_NEW = ["\n>"] * _LOOK
"""What `_in_order` puts after each side's lines: no line equals them, nor one the other."""


MAX_DIFF_LINES = 200
"""The most lines a side of a gap that `save_text` lines up exactly.

About 1.5 ms for 200 alike lines a side; a bigger one is cut at its unique lines first. A
line diff of a whole big file of alike lines took seconds (`_matched_runs`)."""

_EXACT_CELLS = 1_000_000
"""How many line pairs one save lines up exactly in all (`_line_up`), about 60 ms.

Gaps and `_walk`'s windows alike; past it the rest is matched in linear time, so a 1 MB
file of one-letter lines with a difference every few lines saves in about 0.3 s."""


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


OUTSIDE_THE_SERVER = (
    "{file} is a link that leads outside the server folder (to {where}), so Yu'lon will not "
    "read it, back it up or change it. Replace the link with the file itself to edit it here."
)
"""A conf that is a link out of the install is somebody else's file (T573).

Reading it shows that file's text, a save writes to it, and a backup copies it
into the server folder. All three are refused, in one sentence.
"""


def _real(path: Path) -> Path:
    """Where `path` really is, every link on the way followed (a seam: tests stand in Windows')."""
    return Path(os.path.realpath(path))


def check_inside(path: Path, root: Path | None = None) -> None:
    """Refuse when `path` is, or sits under, a link that leads outside `root`.

    `root` is the server folder, `path.parent` when a caller has none. Each step
    from `root` down to `path` is asked `links.is_link()` -- a symlink, or on
    Windows a junction, which Python 3.11 reports as a plain folder -- and only
    a link is followed (`_real`), so a tree with no links costs no resolving. A
    link that leads to somewhere still inside `root` is the player's own tidy
    layout and is allowed.

    Raises:
        TuningError: in a sentence for the player, before anything is read or written.
        OSError: a step could not be looked at (`links.is_link`'s own rule).
    """
    base = path.parent if root is None else root
    try:
        parts = path.relative_to(base).parts
        steps = [base.joinpath(*parts[: i + 1]) for i in range(len(parts))]
    except ValueError:
        steps = [path]
    for step in steps:
        if not links.is_link(step):
            continue
        inside = os.path.normcase(str(_real(base)))
        where = _real(step)
        target = os.path.normcase(str(where))
        if target != inside and not target.startswith(inside.rstrip("\\/") + os.sep):
            raise TuningError(OUTSIDE_THE_SERVER.format(file=path.name, where=where))


def backup(
    path: Path, *, now: datetime | None = None, tag: str = "", root: Path | None = None
) -> Path:
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
    check_inside(path, root)
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
    root: Path | None = None,
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
    # Before the file is opened: a link out of `root` is not read either (T573).
    check_inside(path, root)
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
    made = backup(path, now=now, root=root)
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

    The replaced file KEEPS its own mode (T116). The temp used to be opened at
    the umask default, so a 0600 CMaNGOS conf came back 0644 -- `mangosd.conf`
    carries the database password and is reachable through a module's card.
    Now the temp is created owner-only in the creating call (`private_copy`'s
    rule), the text lands, and it is given the file's own mode before the
    rename, exactly as `conf.replace_file` does. A file not yet on disk keeps
    the owner-only mode.
    """
    temp = path.with_name(f"{path.name}{TEMP_SUFFIX}")
    try:
        mode: int | None = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = None
    try:
        # A temp left by a crash is someone's half-written text, never a file to append to.
        temp.unlink(missing_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if mode is not None:
            # Owner-writable always, as `conf._write` keeps it: a read-only
            # temp is one Windows cannot remove after a failed rename.
            os.chmod(temp, mode | stat.S_IWUSR)
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


def int_problems(text: str, keys: Mapping[str, ConfKey]) -> tuple[str, ...]:
    """Each declared `int` key whose ACTIVE value in this raw conf text fails `check()` (T371).

    The value is the one the server reads (`conf_value()`: first wins, comments
    and sections skipped), checked by the same function the cards use, so the
    raw box and the card cannot disagree. A key the catalog does not declare as
    an `int` is free text and is never looked at.
    """
    found: list[str] = []
    for name, key in keys.items():
        if key.type != "int":
            continue
        value = conf_value(text, name)
        if value is None:
            continue
        try:
            check(key, value)
        except TuningError as exc:
            found.append(str(exc))
    return tuple(found)


VALUE_SENTENCE = "{first} Save it anyway?"


def value_sentence(problems: Sequence[str]) -> str | None:
    """The FIRST bad value in the sentence the confirm asks, or `None` when all are fine."""
    return VALUE_SENTENCE.format(first=problems[0]) if problems else None


LINT_SENTENCE = (
    "Line {line} does not look like a setting: {text!r}. A .conf file holds “Key = Value” "
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
