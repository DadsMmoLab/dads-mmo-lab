"""The Unbound settings the Tuning tab shows: three switches, all off (T554 Y5a).

WoW Unbound's `mod_unbound.conf` carries three opt-in switches. They are read by the
module's own code at world start, so a change lands at the next start:

=========================  ===============================================
key                        what it turns on
=========================  ===============================================
`Unbound.ReagentFree`      casting needs no reagents (soul shards, candles, powders)
`Unbound.InstantSummons`   class summons are instant, free and have no cooldown
`Unbound.AutoBuff`         the `#buffs` chat command that buffs the party
=========================  ===============================================

**Off is the default everywhere.** The shipped `mod_unbound.conf.dist` spells each
`0`; the C++ reads `GetOption<bool>(key, false)` and the Lua reads
`GetConfigValue(key) == "1"`, so a missing file or a missing key also means off.
That is why a key the file does not carry has `current=None` here -- the row says
nothing about the file rather than inventing a `0` -- and `is_on()` answers `False`.

The value is `0` or `1` and nothing else. `tuning.check` for a `bool` also lets
`true` and `false` through, which this module's own readers (`== "1"`) would take
as off while the person who typed `true` meant on; so the check here is narrower.

The write is `tuning.write`'s: one key's value moves, everything else in the file
(comments, order, line endings) stays byte for byte, a backup is taken first, and a
bad value in a save writes none of it. The file must already exist: the install
lays it, and a Save that created the module's conf from three lines would leave the
rest of its documentation out of it.

Nothing here imports Qt. The rows are `tuning.TuningRow`s, drawn by the same
`TuningPanel` card every module gets.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from yulon import tuning
from yulon.manifest import ConfKey

FILE = "env/dist/etc/modules/mod_unbound.conf"
"""The module's conf, server-relative (`confs_from_dist` lays it from the `.conf.dist`)."""

CARD = ("core", "unbound")
"""The card's `(family, module_id)` on the Tuning tab."""

CARD_NAME = "Unbound"

DEFAULT = "0"
"""Every switch is off unless the conf says `1`."""

TAKES_EFFECT = "Takes effect at the next start."

_SWITCHES: tuple[tuple[str, str, str], ...] = (
    (
        "Unbound.ReagentFree",
        "Free casting reagents",
        "Spells that need soul shards, candles, powders and the like cast without them.",
    ),
    (
        "Unbound.InstantSummons",
        "Instant, free class summons",
        "Summoning a pet or demon is instant, costs nothing and has no cooldown.",
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


def is_on(row: tuning.TuningRow) -> bool:
    """Whether this row's switch is on: only an explicit `1` is. No value means off."""
    return row.current == "1"


def write(server_dir: Path, edits: Mapping[str, str], *, now: datetime | None = None) -> Path:
    """Set these switches in `mod_unbound.conf` and return the backup's path.

    Refused, before anything is written: a key that is not one of the three, a value
    other than `0` or `1`, and a conf that is not on disk yet.
    """
    spec = conf_keys()
    for key, value in edits.items():
        if key not in spec:
            raise tuning.TuningError(f"{key}: is not an Unbound setting")
        if value not in ("0", "1"):
            raise tuning.TuningError(f"{key}: `{value}` is not 0 or 1 (0 is off, 1 is on)")
    path = server_dir / FILE
    if not path.is_file():
        raise tuning.TuningError(
            f"mod_unbound.conf is not there yet ({path}); install WoW Unbound first"
        )
    return tuning.write(path, edits, spec=spec, now=now)


def _read(path: Path) -> str | None:
    """The file's exact text, or `None` when it is absent, unreadable or not UTF-8."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None
