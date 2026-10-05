"""The Tuning tab's built-in Server rates card: XP, gold, item drops, reputation, honor (T302).

Before this, XP rates came from the "XP Rate Customization" mod (WotLK, TBC,
Vanilla and Tortoise; Centurion had none), and gold, drops, reputation and honor
could only be changed by editing the world server's conf by hand. This card is
built in for every game and writes the world server's own conf file.

**Every key, default and range below was read off the pinned fork, never guessed**
(2026-10-05, at the commit `read_at()` names; `test_server_rates` turns red when
a pin moves, so the reading is repeated rather than inherited):

=============  ===========================  ======================================
game           conf (server dir)            where the keys are read and spelled
=============  ===========================  ======================================
wow-wotlk      env/dist/etc/worldserver.conf  mod-playerbots/azerothcore-wotlk:
                                              `worldserver.conf.dist:2439` (rep),
                                              `:2499-2502` (XP), `:2573` (honor),
                                              `:2850-2858` (drops, gold);
                                              `WorldConfig.cpp:41-55,62,64,88,119`
wow-tbc        etc/mangosd.conf             cmangos/mangos-tbc:
                                              `mangosd.conf.dist.in:1560-1594`;
                                              `World/World.cpp:409-431,458`
wow-vanilla    etc/mangosd.conf             cmangos/mangos-classic:
                                              `mangosd.conf.dist.in:1499-1525`;
                                              `World/World.cpp:411-425,452`
wow-tortoise   etc/mangosd.conf             tortoise-wow/tortoise-wow:
                                              `mangosd.conf.dist.in:1702-1716,1744,1776`;
                                              `World.cpp:999-1012,1042`
wow-centurion  etc/worldserver.conf         thomasjteachey/TrinityCore112:
                                              `worldserver.conf.dist:2492-2520,2585,2606`;
                                              `World/World.cpp:569-582,593,620`
=============  ===========================  ======================================

All eleven keys exist under the same names on all five forks, and every fork's
compiled default for each is `1.0f` -- which is what a key missing from the file
means, so it is the `default` a row shows. (Tortoise's own `.dist` ships
`Rate.Drop.Item.Uncommon = 2`; that is the file's value, and Reset to default
puts it back, but the server's fallback is still 1.) Vanilla has no `.Vanilla` /
`.BC` XP twins (`World.cpp:422-424`); TBC has them and multiplies them ON TOP of
these (`Maps/Map.cpp:1493-1534`), which its rows say.

**Item drops: the five qualities that drop in play** -- grey, white, green, blue
and purple. Legendary and Artifact are hand-placed loot, `Referenced` and the
amount keys are loot-table plumbing, and CMaNGOS's `Rate.Drop.Item.Quest` is
quest items rather than a quality; they stay in the file. No fork has one key
for "all items".

**The range, 0 to 100.** The floor is the forks' where they state one: CMaNGOS
reads every key here but honor (and, on Tortoise, the XP and reputation keys)
through `setConfigPos`, which refuses a negative value (`World.cpp:2590-2598` on
TBC). AzerothCore and TrinityCore check none of them, and a negative rate there
takes experience or money away rather than giving less, so the floor is 0 on
every game. NO fork states a ceiling. 100 is Yu'lon's own, and it is a safety
bound and not a fact about the server: it keeps a typo (`1000` for `10`) from
reaching a live realm, and leaves the file's own editor for anybody who means it.

**The XP Rate Customization mod (owner choice recorded in T302).** The card owns
these keys. When the mod is installed its three XP rows stay on its own card,
read-only, saying the Server rates card sets them (`yield_to_card`), so the tab
has one writer per key. The mod itself keeps working: installing it still writes
the XP multiplier it asks for, removing it still puts the three keys back to 1,
and Reset to default still keeps a key an installed module declares (owner
decision 5, `reset_defaults.carry_module_keys`).

Nothing here imports Qt. The rows are `tuning.TuningRow`s, drawn by the same
`TuningPanel` card every module gets, and saved by the same `save_tuning`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from yulon import tuning
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger
from yulon.manifest import ConfKey

logger = get_logger(__name__)

CARD = ("core", "server-rates")
"""The card's `(family, module_id)` on the Tuning tab."""

CARD_NAME = "Server rates"

FLOOR = 0
CEILING = 100
"""The range every row accepts (see the module's docstring for where each end comes from)."""

DEFAULT = "1"
"""Every fork's compiled default for every key here (`1.0f`), as the confs spell it."""

ON_THE_RATES_CARD = (
    "The Server rates card sets this now, so change it there. This mod wrote it when it "
    "was installed and puts it back to 1 when it is removed."
)
"""What a module's row says when this card owns its key (`yield_to_card`)."""

_HOW_MUCH = "1 is the normal amount, 2 is double, 0.5 is half."


@dataclass(frozen=True)
class _Rate:
    key: str
    label: str
    explain: str


_RATES: tuple[_Rate, ...] = (
    _Rate("Rate.XP.Kill", "XP from kills", f"Experience for killing monsters. {_HOW_MUCH}"),
    _Rate("Rate.XP.Quest", "XP from quests", f"Experience for finishing quests. {_HOW_MUCH}"),
    _Rate(
        "Rate.XP.Explore",
        "XP from exploring",
        f"Experience for discovering new places. {_HOW_MUCH}",
    ),
    _Rate("Rate.Drop.Money", "Gold dropped", f"Money that monsters drop. {_HOW_MUCH}"),
    _Rate(
        "Rate.Drop.Item.Poor",
        "Grey item drops",
        f"How often grey (poor) items drop. {_HOW_MUCH}",
    ),
    _Rate(
        "Rate.Drop.Item.Normal",
        "White item drops",
        f"How often white (common) items drop. {_HOW_MUCH}",
    ),
    _Rate(
        "Rate.Drop.Item.Uncommon",
        "Green item drops",
        f"How often green (uncommon) items drop. {_HOW_MUCH}",
    ),
    _Rate(
        "Rate.Drop.Item.Rare",
        "Blue item drops",
        f"How often blue (rare) items drop. {_HOW_MUCH}",
    ),
    _Rate(
        "Rate.Drop.Item.Epic",
        "Purple item drops",
        f"How often purple (epic) items drop. {_HOW_MUCH}",
    ),
    _Rate("Rate.Reputation.Gain", "Reputation gained", f"Reputation you earn. {_HOW_MUCH}"),
    _Rate(
        "Rate.Honor",
        "Honor gained",
        f"Honor earned in player-versus-player combat. {_HOW_MUCH}",
    ),
)

_ALL_KEYS = tuple(rate.key for rate in _RATES)

_TBC_TIERS = (
    "On this server {key}.Vanilla and {key}.BC also multiply it, for old-world and for "
    "Outland content; they stay as they are in the file."
)
_TURTLE_MODE = "Characters on the Slow and Steady challenge do not get this boost."
"""Tortoise's kill XP skips this rate for that challenge (`src/game/Formulas.h:140-166`)."""


@dataclass(frozen=True)
class _GameRates:
    """One game's reading: its world conf, the pin it was read at, its keys, its notes."""

    file: str
    repo: str
    rev: str
    keys: tuple[str, ...]
    notes: Mapping[str, str]


_GAMES: dict[str, _GameRates] = {
    "wow-wotlk": _GameRates(
        file="env/dist/etc/worldserver.conf",
        repo="mod-playerbots/azerothcore-wotlk",
        rev="7f12e89ee5f467a50e62eba1d525eac7dc953d03",
        keys=_ALL_KEYS,
        notes={},
    ),
    "wow-tbc": _GameRates(
        file="etc/mangosd.conf",
        repo="cmangos/mangos-tbc",
        rev="75f9ae68edd5ea94dda5f7f0ddf140f1acd94a6f",
        keys=_ALL_KEYS,
        notes={
            key: _TBC_TIERS.format(key=key)
            for key in ("Rate.XP.Kill", "Rate.XP.Quest", "Rate.XP.Explore")
        },
    ),
    "wow-vanilla": _GameRates(
        file="etc/mangosd.conf",
        repo="cmangos/mangos-classic",
        rev="8ec338a1704e7dcb1c0213eb7ed58f9231ade40f",
        keys=_ALL_KEYS,
        notes={},
    ),
    "wow-tortoise": _GameRates(
        file="etc/mangosd.conf",
        repo="tortoise-wow/tortoise-wow",
        rev="187af788177aa2f9f0e61eb8c5b9653d8f4f7199",
        keys=_ALL_KEYS,
        notes={"Rate.XP.Kill": _TURTLE_MODE},
    ),
    "wow-centurion": _GameRates(
        file="etc/worldserver.conf",
        repo="thomasjteachey/TrinityCore112",
        rev="faac5fc9b0fe0934c26f08231793ca607d38327d",
        keys=_ALL_KEYS,
        notes={},
    ),
}
"""Per catalog id, because every fact here is a fact about that entry's pinned fork."""


def _game(entry: CatalogEntry) -> _GameRates | None:
    return _GAMES.get(entry.id)


def read_at(entry: CatalogEntry) -> tuple[str, str] | None:
    """`(repo, rev)` the keys were read at for this game, or `None` without a card."""
    game = _game(entry)
    return None if game is None else (game.repo, game.rev)


def card_file(entry: CatalogEntry) -> str | None:
    """The world server's conf this game's card writes, server-relative, or `None`."""
    game = _game(entry)
    return None if game is None else game.file


def conf_keys(entry: CatalogEntry) -> dict[str, ConfKey]:
    """The card's keys as `tuning.check()` declarations, in the card's order."""
    game = _game(entry)
    if game is None:
        return {}
    keys: dict[str, ConfKey] = {}
    for rate in _RATES:
        if rate.key not in game.keys:
            continue
        note = game.notes.get(rate.key)
        keys[rate.key] = ConfKey(
            key=rate.key,
            default=DEFAULT,
            label=rate.label,
            explain=rate.explain if note is None else f"{rate.explain} {note}",
            type="float",
            min=FLOOR,
            max=CEILING,
        )
    return keys


def _read(path: Path) -> str | None:
    """The file's exact text, or `None` when it cannot be read as UTF-8 (`tuning._read`'s rule)."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(f"server rates: could not read {path}: {exc}")
        return None


def rows(entry: CatalogEntry, server_dir: Path) -> tuple[tuning.TuningRow, ...]:
    """The card's rows, read off the world conf -- or none while that file is not on disk.

    No file, no card: before an install has written the conf there is nothing for
    a rate to change, and Save would create the server's main conf from eleven
    lines (`TuningPanel`'s raw editor keeps the same rule, `_tuning_files`).
    """
    file = card_file(entry)
    if file is None or not (server_dir / file).is_file():
        return ()
    text = _read(server_dir / file)
    return tuple(
        tuning.TuningRow(
            module_id=CARD[1],
            module_name=CARD_NAME,
            family=CARD[0],
            file=file,
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
        for key, spec in conf_keys(entry).items()
    )


def yield_to_card(
    module_rows: Iterable[tuning.TuningRow], card_rows: Iterable[tuning.TuningRow]
) -> tuple[tuning.TuningRow, ...]:
    """Module rows, with every one this card also writes made read-only and saying so.

    The same key in the same FILE, both: a module that names `Rate.XP.Kill` in
    a conf of its own is a different setting and keeps its row. For the
    display only -- the view keeps the unchanged rows as the answer to "which
    keys does an installed module declare", which Reset to default reads.
    """
    owned = {(row.file, row.key) for row in card_rows}
    return tuple(
        (replace(row, read_only_reason=ON_THE_RATES_CARD) if (row.file, row.key) in owned else row)
        for row in module_rows
    )
