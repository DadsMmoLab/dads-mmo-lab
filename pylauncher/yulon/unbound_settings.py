"""The Unbound settings the Tuning tab shows: three switches, all off (T554 Y5a).

WoW Unbound's `mod_unbound.conf` carries three opt-in switches. They are read by the
module's own code at world start, so a change lands at the next start:

=========================  ===============================================
key                        what it turns on
=========================  ===============================================
`Unbound.ReagentFree`      casting needs no reagents (soul shards, candles, powders)
`Unbound.InstantSummons`   class summons are instant, cost no mana and have no cooldown
`Unbound.AutoBuff`         the `#buffs` chat command: automatic buffs for a player and their summons
=========================  ===============================================

Instant summons does NOT remove reagents: a Soul Shard or Infernal Stone is still used
unless `Unbound.ReagentFree` is on too, so no text here calls it "free" (U3 review).

**Off is the default everywhere.** The shipped `mod_unbound.conf.dist` spells each
`0`; the C++ reads `GetOption<bool>(key, false)` and `dml_autobuff.lua` turns on only
for `1` or `true` (any case, which ALE lowers), so a missing file or a missing key also means off.
That is why a key the file does not carry has `current=None` here -- the row says
nothing about the file rather than inventing a `0` -- and `is_on()` answers `False`.

Both readers take `1`/`true` as on and `0`/`false` as off, so a hand-edited `true` or
`false` is a value the module reads, and the switch shows it as such. The Tuning
tab's switch flips a value in the file's own spelling; `write()` puts the module's
own `0`/`1` back, and refuses only a value outside those four words, which at least
one of the module's readers cannot take (T554 rework).

The write is `tuning.write`'s: one key's value moves, everything else in the file
(comments, order, line endings) stays byte for byte, a backup is taken first, and a
bad value in a save writes none of it. The file must already exist: the install
lays it, and a Save that created the module's conf from three lines would leave the
rest of its documentation out of it.

Nothing here imports Qt. The rows are `tuning.TuningRow`s, drawn by the same
`TuningPanel` card every module gets.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from yulon import tuning
from yulon.catalog.catalog import CatalogEntry
from yulon.manifest import ConfKey
from yulon.module_health import Switch

FILE = "env/dist/etc/modules/mod_unbound.conf"
"""The module's conf, server-relative (`confs_from_dist` lays it from the `.conf.dist`)."""

CARD = ("core", "unbound")
"""The card's `(family, module_id)` on the Tuning tab."""

CARD_NAME = "Unbound"

DEFAULT = "0"
"""Every switch is off unless the conf says `1` (or `true`)."""

_WRITTEN_AS = {"1": "1", "true": "1", "0": "0", "false": "0"}
"""What `write()` puts in the file for each value the module reads, by its lower case."""

TAKES_EFFECT = "Takes effect at the next start."

_SWITCHES: tuple[tuple[str, str, str], ...] = (
    (
        "Unbound.ReagentFree",
        "Free casting reagents",
        "Spells that need soul shards, candles, powders and the like cast without them.",
    ),
    (
        "Unbound.InstantSummons",
        "Instant class summons",
        "Summoning a pet or demon is instant, costs no mana and has no cooldown. "
        "It still uses reagents (soul shards and the like) unless free casting reagents is on.",
    ),
    (
        "Unbound.AutoBuff",
        "#buffs auto-buff command",
        "A player who types #buffs on in chat has their buffs, and their summons' buffs, "
        "renewed on their own.",
    ),
)


def conf_keys() -> dict[str, ConfKey]:
    """The three switches as `tuning.check()` declarations, in the card's order."""
    return {
        key: ConfKey(
            key=key,
            default=DEFAULT,
            label=label,
            explain=f"{explain} {TAKES_EFFECT}",
            type="bool",
        )
        for key, label, explain in _SWITCHES
    }


def rows(server_dir: Path) -> tuple[tuning.TuningRow, ...]:
    """The card's rows. `current` is `None` for a key the file does not say, or no file."""
    text = _read(server_dir / FILE)
    return tuple(
        tuning.TuningRow(
            module_id=CARD[1],
            module_name=CARD_NAME,
            family=CARD[0],
            file=FILE,
            key=key,
            label=spec.label or key,
            explain=spec.explain,
            type=spec.type,
            min=spec.min,
            max=spec.max,
            default=spec.default,
            current=None if text is None else tuning.conf_value(text, key),
            installed=True,
            backend="conf",
            read_only_reason=None,
        )
        for key, spec in conf_keys().items()
    )


_RUNNING_LINES: dict[str, tuple[str, re.Pattern[str]]] = {
    "Unbound.ReagentFree": ("free reagents", re.compile(r"\[UNBOUND\] free reagents: (on|off)\b")),
    "Unbound.InstantSummons": (
        "instant summons",
        re.compile(r"\[UNBOUND\] instant summons: (on|off)\b"),
    ),
    # `dml_autobuff.lua` prints `off (Unbound.AutoBuff = 0)` or `v3 loaded` and nothing else.
    "Unbound.AutoBuff": ("#buffs", re.compile(r"\[dml_autobuff\] (off|v\d+ loaded)")),
}
"""Per switch: its short name, and the line the module prints at world start saying how it started
(`UnboundReagentFree.cpp` OnStartup, `dml_autobuff.lua`, at the mod-unbound pin)."""


def running_state(log_text: str) -> dict[str, bool | None]:
    """How each switch started in the run `log_text` belongs to; `None` where the log is silent.

    The conf only says what the NEXT start will use. This run's own lines say what it used, and a
    switch whose line is not in the log is `None` (not said), never `False`: a log that did not say
    is not a switch that is off. The last line wins, in case a script reloaded.
    """
    state: dict[str, bool | None] = {}
    for key, (_label, pattern) in _RUNNING_LINES.items():
        found = pattern.findall(log_text)
        state[key] = None if not found else found[-1] != "off"
    return state


def switches(server_dir: Path, log_text: str) -> tuple[Switch, ...]:
    """The switches as this run started them (its log) beside the conf, in the card's order."""
    running = running_state(log_text)
    return tuple(
        Switch(_RUNNING_LINES[row.key][0], running[row.key], is_on(row)) for row in rows(server_dir)
    )


def shown_for(entry: CatalogEntry) -> bool:
    """Whether this entry has the card: its install makes `mod_unbound.conf` from the `.dist`.

    Read off the entry's data (`confs_from_dist`), never off its id, so a second server that
    carries the module gets the card and WotLK, whose data does not name the conf, does not.
    """
    native = entry.install.native
    block = native.azerothcore if native is not None else None
    return block is not None and FILE in block.confs_from_dist


def is_on(row: tuning.TuningRow) -> bool:
    """Whether this row's switch is on: `1` or `true` in any case. `0`, `false`, no value: off.

    Any case because every reader lowers it: the C++ `GetOption<bool>` compares without
    case, and ALE's `GetConfigValue` turns `True` into the boolean `true` before
    `dml_autobuff.lua` sees it (`GlobalMethods.h:77-84` at 1cb86c96).
    """
    return row.current is not None and row.current.strip().lower() in ("1", "true")


def write(server_dir: Path, edits: Mapping[str, str], *, now: datetime | None = None) -> Path:
    """Set these switches in `mod_unbound.conf` and return the backup's path.

    `true` and `false` (any case) are written as `1` and `0`. Refused, before anything is
    written: a key that is not one of the three, any other value, and a conf that is not
    on disk yet. The refusal does not repeat the value: on the Tuning tab nobody typed
    it, they ticked a box.
    """
    spec = conf_keys()
    written: dict[str, str] = {}
    for key, value in edits.items():
        if key not in spec:
            raise tuning.TuningError(f"{key}: is not an Unbound setting")
        as_written = _WRITTEN_AS.get(value.lower()) if value == value.strip() else None
        if as_written is None:
            raise tuning.TuningError(
                f"{key}: this switch can only be saved as on or off, and the value it was "
                "given is neither"
            )
        written[key] = as_written
    path = server_dir / FILE
    if not path.is_file():
        raise tuning.TuningError(
            f"mod_unbound.conf is not there yet ({path}); install WoW Unbound first"
        )
    return tuning.write(path, written, spec=spec, now=now)


def _read(path: Path) -> str | None:
    """The file's exact text, or `None` when it is absent, unreadable or not UTF-8."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None
