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
                                              `worldserver.conf.dist:2525-2553,2618,2639`;
                                              `World/World.cpp:570-583,594,621`
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
has one control per key. The mod itself keeps working: installing it still writes
the XP multiplier it asks for, removing it still puts the three keys back to 1,
and Reset to default still keeps a key an installed module declares (owner
decision 5, `reset_defaults.carry_module_keys`). Those two are a person's own
presses, not a second control, and while the mod is installed the card's XP rows
SAY both (`shared_with`), where the value is changed (Codex adversarial review,
2026-10-05, asked for the mod's removal to stop writing or for reset to drop the
carry; both would overrule what the mod and owner decision 5 promise, so this
says it instead).

Nothing here imports Qt. The rows are `tuning.TuningRow`s, drawn by the same
`TuningPanel` card every module gets, and saved by the same `save_tuning`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from yulon import tuning
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger
from yulon.manifest import ConfKey, Manifest

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

SHARED_WITH = (
    "{module} is installed and also sets this. While it is installed, Reset to default "
    "keeps this value, and removing {module} sets it back to 1."
)
"""What the card's own row adds while an installed module names the same key (`shared_with`).

True of the one module that does, XP Rate Customization: its remove patches write 1
and owner decision 5 carries its keys through a reset. `test_server_rates` fails if
another manifest comes to name a rate this card writes, or that mod's removal stops
writing 1.
"""

PROMPT_REPLACES = (
    "Your answer replaces {labels} on the Tuning tab's Server rates card. The box starts "
    "at what that card says now."
)
"""The note on a module's install question whose answer is written to a key this card
writes (cold review, 2026-10-05: installing XP Rate Customization after setting XP on
the card put the mod's default 1 over the card's value, and the question did not say)."""

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
_QUEST_MONEY = "On this server it also multiplies the money quests reward."
"""CMaNGOS's `Quest::GetRewOrReqMoney()` scales a quest's money reward by this key:
mangos-tbc `src/game/Quests/QuestDef.cpp:216-222`, mangos-classic `:211-217`, Tortoise
`src/game/QuestDef.cpp:204-210`. AzerothCore and TrinityCore keep a key of their own for
it (`Rate.RewardQuestMoney`, AC `QuestDef.cpp:255`; `RATE_MONEY_QUEST`, TC112
`QuestDef.cpp:317-319`), so theirs say nothing (Codex adversarial review, 2026-10-05)."""

_LEVEL_CAP_MONEY = (
    "On this server it also multiplies the money quests reward, and the gold a quest gives "
    "instead of XP at the level cap."
)
"""Tortoise also scales the max-level XP-to-gold conversion (`src/game/QuestDef.cpp:212-222`)."""

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
        rev="15b6ddb4ec9e443d49f4e438af73782ce5c16491",
        keys=_ALL_KEYS,
        notes={
            **{
                key: _TBC_TIERS.format(key=key)
                for key in ("Rate.XP.Kill", "Rate.XP.Quest", "Rate.XP.Explore")
            },
            "Rate.Drop.Money": _QUEST_MONEY,
        },
    ),
    "wow-vanilla": _GameRates(
        file="etc/mangosd.conf",
        repo="cmangos/mangos-classic",
        rev="8ec338a1704e7dcb1c0213eb7ed58f9231ade40f",
        keys=_ALL_KEYS,
        notes={"Rate.Drop.Money": _QUEST_MONEY},
    ),
    "wow-tortoise": _GameRates(
        file="etc/mangosd.conf",
        repo="tortoise-wow/tortoise-wow",
        rev="187af788177aa2f9f0e61eb8c5b9653d8f4f7199",
        keys=_ALL_KEYS,
        notes={"Rate.XP.Kill": _TURTLE_MODE, "Rate.Drop.Money": _LEVEL_CAP_MONEY},
    ),
    "wow-centurion": _GameRates(
        file="etc/worldserver.conf",
        repo="thomasjteachey/TrinityCore112",
        rev="56fe34fa8f4ad655d297e132a520b7902feb26c2",
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


def shared_with(
    card_rows: Iterable[tuning.TuningRow], module_rows: Iterable[tuning.TuningRow]
) -> tuple[tuning.TuningRow, ...]:
    """The card's rows, each one an installed module also names saying so (`SHARED_WITH`).

    The other half of `yield_to_card` (Codex adversarial review, 2026-10-05): the
    card is the one CONTROL for the key, but the module's removal and Reset to
    default still act on it, and the row a person changes it on is where that is said.
    """
    names: dict[tuple[str, str], str] = {}
    for row in module_rows:
        if row.installed and row.backend == "conf":
            names.setdefault((row.file, row.key), row.module_name)
    shared: list[tuning.TuningRow] = []
    for row in card_rows:
        name = names.get((row.file, row.key))
        if name is None:
            shared.append(row)
            continue
        said = SHARED_WITH.format(module=name)
        shared.append(replace(row, explain=f"{row.explain} {said}" if row.explain else said))
    return tuple(shared)


_WHOLE_TEMPLATE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
"""A conf key default that is exactly one prompt's answer, `{kill}` (`apply`'s template)."""


def prompt_values(manifest: Manifest, card_rows: Iterable[tuning.TuningRow]) -> dict[str, str]:
    """Prompt key -> what the card's file says now, for each question whose answer IS a card key.

    Read off the manifest's own conf rows: a key whose `default` is exactly one
    `{prompt}` is written with that answer, so the question should start at the
    value the card shows rather than the mod's default (or an answer from an
    install the card has since overruled). The first card key wins when one
    answer feeds several (Tortoise's `xp_rate` feeds all three XP keys: it starts
    at XP from kills). A key the file does not carry gives no value.
    """
    current = {(row.file, row.key): row.current for row in card_rows}
    found: dict[str, str] = {}
    for conf in manifest.conf:
        for key in conf.keys:
            match = _WHOLE_TEMPLATE.fullmatch(key.default or "")
            value = current.get((conf.file, key.key))
            if match is not None and value is not None:
                found.setdefault(match.group(1), value)
    return found


SAME_NUMBER = "It starts at {first}. {others} will be set to the same number."
"""Added when ONE answer feeds several card rows the card now holds at different values
(Tortoise's `xp_rate` feeds all three XP keys): OK puts the first one's value on all
of them (scoped re-review, 2026-10-05)."""


def _and(labels: Sequence[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _fed(
    manifest: Manifest, card: Mapping[tuple[str, str], tuning.TuningRow]
) -> dict[str, list[tuning.TuningRow]]:
    """Answer key -> the card rows that answer is written to, in the manifest's order."""
    fed: dict[str, list[tuning.TuningRow]] = {}
    for conf in manifest.conf:
        for key in conf.keys:
            match = _WHOLE_TEMPLATE.fullmatch(key.default or "")
            row = card.get((conf.file, key.key))
            if match is not None and row is not None:
                fed.setdefault(match.group(1), []).append(row)
    return fed


def prompt_note(manifest: Manifest, card_rows: Sequence[tuning.TuningRow]) -> str | None:
    """`PROMPT_REPLACES` naming the card rows this module's answers are written to, or `None`.

    Plus `SAME_NUMBER` for an answer that feeds several rows the card holds at
    different values, because the box can start at only one of them.
    """
    fed = _fed(manifest, {(row.file, row.key): row for row in card_rows})
    labels: list[str] = []
    for rows in fed.values():
        for row in rows:
            if row.label not in labels:
                labels.append(row.label)
    if not labels:
        return None
    said = PROMPT_REPLACES.format(labels=_and(labels))
    for rows in fed.values():
        if len(rows) > 1 and len({row.current for row in rows}) > 1:
            first, *others = rows
            said += " " + SAME_NUMBER.format(
                first=first.label, others=_and([row.label for row in others])
            )
    return said


def answer_problem(
    entry: CatalogEntry, manifest: Manifest, answers: Mapping[str, str] | None
) -> str | None:
    """Why a module's answer cannot be written to a key this card writes, or `None`.

    The mod's own question checks only its `kind` -- Tortoise's `xp_rate` is a
    `string`, and WotLK's `float` takes `1e-45` -- so a value the core's parser
    throws on at world start reached `Rate.XP.*` that way. Each answer that is
    written to a card key is held to that key's own rule (`tuning.check`: a plain
    decimal, at most 4 places, inside 0 to 100), whether or not the conf is on
    disk yet. The first refusal, in `tuning`'s own words.
    """
    file = card_file(entry)
    if file is None or not answers:
        return None
    spec = conf_keys(entry)
    for conf in manifest.conf:
        if conf.file != file:
            continue
        for key in conf.keys:
            match = _WHOLE_TEMPLATE.fullmatch(key.default or "")
            if match is None or key.key not in spec or match.group(1) not in answers:
                continue
            try:
                tuning.check(spec[key.key], answers[match.group(1)])
            except tuning.TuningError as exc:
                return str(exc)
    return None
