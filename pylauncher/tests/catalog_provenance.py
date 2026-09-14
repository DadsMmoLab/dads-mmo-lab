"""Where every value the catalog writes down about a tree came from.

Test support, not shipped code — it sits beside `write_sites.py` for the same
reason and by the same precedent: `write_sites.py` holds the ledger's RULES
while `pyplan/write-ledger.md` holds the table a person reads, and this module
is the other half of that pairing for the catalog.

**The problem this exists for.** Phase 8's exit line asks that "every value
still marked unverified in the catalog's operations block [be] replaced by a
measured one, and that set enumerated by name in the catalog test rather than
left to memory" (`pyplan/checklist.md`). Nothing enumerated it. "Measured"
lived in prose, in the model docstrings, and the prose is written PER FIELD
while the values are per field PER GAME — `Equipped`'s docstring says the flat
shape was "measured on the TBC install 2026-09-06 and on the Vanilla one
2026-09-07", which is four trees' worth of values and two trees' worth of
measurements in one sentence, and no way to ask which of the four a given value
belongs to. So a value could be measured here, read off somebody's header, or
copied from a sibling, and all three read identically in the file.

That last one is the expensive one. `per-tree facts are measured per tree,
never inherited from a sibling emulator` is a rule this project has already
paid a night for, and an inherited value is invisible precisely because it is
CORRECT-LOOKING: 8.4c found `mail_item_cap: 1` sitting in the catalog as what
its own README calls "a **source-cited prediction** … A prediction the server
has never been asked is not a measurement."

**The catalog wrote this design out by hand once, in prose, and nothing could
read it.** `AzerothCoreData.world_env`'s description (`catalog.py:202`) says
"PROVENANCE: WotLK carried 1600/2000, copied from the ONE proven yulon-ubuntu
install … Never measured on another machine and never measured at all for RAM …
Still owed an RSS reading by the first gate" — an inherited kind, a citation and
an owed debt, in one field, reachable by no test. This module is that sentence
made uniform.

**The three kinds, and why three.** The vocabulary is 8.4c's own, generalised:

* `measured-on` — this tree's own running install was ASKED and this is its
  answer. Console, SOAP or SQL: what makes it a measurement is the live server,
  not the transport. The citation names a gate write-up in the maintainers'
  local notes (`.notes/`, not published); those notes check that it resolves.
* `read-from-source` — read off this tree's own source at a named `path:line`.
  A prediction. Honest, useful, and not the same thing as an answer. Not
  resolvable from here: we do not vendor the emulator trees, so the shape is
  checked and the content is not, and that limit is stated rather than hidden.
* `inherited` — carried over from a sibling tree; nobody asked this one. The
  dangerous kind, and the reason the marker is worth having at all.

**What is NOT here.** A fourth kind for "measured, but the reading is not in
the tree" was considered and refused: `wow-tortoise:accounts.scheme` is exactly
that case, and it is in `OWED` below rather than given a kind of its own,
because a kind that means "trust the docstring" is the state this module
replaces. Owing it keeps it on a list somebody has to answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["measured-on", "read-from-source", "inherited"]

BLOCKS = ("play", "accounts")
"""The catalog blocks this table covers.

Two of the four per-tree blocks, which is the scope the guard was asked for.
`operations` and `observability` are the other two and are NOT marked; the
design page carries the count and the reason. Named here rather than left to
be inferred from what happens to have rows.
"""

SOURCE_CITE = re.compile(r"^[\w./+-]+\.(?:cpp|h|hpp|cc|lua|sql|conf|ini):\d+(?:-\d+)?$")
"""`path:line` or `path:first-last` into an emulator tree this repo does not vendor.

Shape only. There is no file to open, and a guard that pretended otherwise
would be asserting about its own regex. What the shape does buy is that a
`read-from-source` row cannot quietly hold a sentence of English instead of a
citation, which is the way this field would actually rot.
"""


@dataclass(frozen=True)
class Provenance:
    """Where one written-down value came from."""

    kind: Kind
    cite: str
    """A path under the local `.notes/` for `measured-on`, a `path:line` for
    `read-from-source`, and the game id it was copied from for `inherited`."""
    note: str = ""
    """What the citation does not say by itself. Optional, and empty by default
    rather than repeating the citation in words."""


# -- the table ------------------------------------------------------------
#
# Keyed `"<game id>:<block>.<dotted path>"`. The dotted path is the value's
# position inside the block as the FILE writes it, so `accounts.level.max_level`
# is a leaf and `accounts.level` is not: a guard about values has to be keyed by
# values, and a block-level row would let a field be added underneath it without
# anything going red.

PROVENANCE: dict[str, Provenance] = {
    # -- wow-wotlk (AzerothCore) — 8.4a on yulon-ubuntu, 8.3a on both boxes ---
    "wow-wotlk:play.equipped.template_column": Provenance(
        "measured-on",
        "gates/8.4a-wotlk-yulon-ubuntu-2026-09-07/README.md",
        note="the joined shape carried a real gear set: `Send Aevret's 12 worn items (1 mail)`",
    ),
    "wow-wotlk:play.equipped.instance_table": Provenance(
        "measured-on",
        "gates/8.4a-wotlk-yulon-ubuntu-2026-09-07/README.md",
        note=(
            "same press; the flat shape on this tree is an `Unknown column` error, not a wrong "
            "answer"
        ),
    ),
    "wow-wotlk:play.equipped.inventory_column": Provenance(
        "measured-on", "gates/8.4a-wotlk-yulon-ubuntu-2026-09-07/README.md"
    ),
    "wow-wotlk:play.teleport_command": Provenance(
        "measured-on",
        "gates/8.4a-wotlk-yulon-ubuntu-2026-09-07/README.md",
        note="`teleport name Asfgg Orgrimmar` moved a character a person was playing",
    ),
    "wow-wotlk:play.rename_command": Provenance(
        "measured-on",
        "gates/8.4a-wotlk-yulon-ubuntu-2026-09-07/README.md",
        note="`character rename Asfgg`, and the prompt at the next login in the client",
    ),
    "wow-wotlk:play.set_level_command": Provenance(
        "measured-on",
        "gates/8.4a-wotlk-yulon-ubuntu-2026-09-07/README.md",
        note=(
            "`character level Asfgg 20`, read back in the client's portrait; level 53 -> 60 in the "
            "row"
        ),
    ),
    "wow-wotlk:accounts.level.table": Provenance(
        "measured-on",
        "gates/8.3a-wotlk-yulon-ubuntu-2026-09-07/README.md",
        note="the level change read back out of `account_access` as `107  2  -1`",
    ),
    "wow-wotlk:accounts.level.account_column": Provenance(
        "measured-on",
        "gates/8.3a-wotlk-yulon-ubuntu-2026-09-07/README.md",
        note="the join found the row for the account the press had just changed",
    ),
    "wow-wotlk:accounts.level.level_column": Provenance(
        "measured-on",
        "gates/8.3a-wotlk-yulon-ubuntu-2026-09-07/README.md",
        note="and the server agreed with the row: `.account info GATE83A` -> `GMLevel: 2`",
    ),
    # `accounts.scheme` is NOT here for this game, and the omission is the rule
    # working rather than a gap: `wow-wotlk` does not write the key at all, so
    # `azerothcore` is `Accounts.scheme`'s own default and belongs to that
    # field's docstring. 8.3a's Windows half did measure it -- the stored
    # verifier recomputed by hand from the salt matched the new password byte
    # for byte -- which makes this the sharpest example of the hole `leaves()`
    # names: a real per-tree measurement sitting outside the guard because
    # nobody had to type the value.
    # -- wow-tbc (CMaNGOS TBC) — 8.4b and 8.3b on m910q ----------------------
    "wow-tbc:play.equipped.template_column": Provenance(
        "measured-on",
        "gates/8.4b-tbc-m910q-2026-09-07/README.md",
        note=(
            "`character_inventory` carries `item_template` beside `item` here, so the read is flat"
        ),
    ),
    "wow-tbc:play.teleport_command": Provenance(
        "measured-on",
        "gates/8.4b-tbc-m910q-2026-09-07/README.md",
        note="and the sibling's spelling was asked too: `teleport name` closed the connection",
    ),
    "wow-tbc:play.mail_item_cap": Provenance(
        "measured-on",
        "gates/8.4b-tbc-m910q-2026-09-07/README.md",
        note=(
            "measured by EXCEEDING it (`mail-cap.py`): 12 sent, 13 refused with no mail and no "
            "split"
        ),
    ),
    "wow-tbc:play.rename_command": Provenance(
        "measured-on",
        "gates/8.4b-online-rename-tbc-m910q-2026-09-08/README.md",
        note="the owed re-press, on an ONLINE character; 8.4b's first press had it offline",
    ),
    "wow-tbc:play.set_level_command": Provenance(
        "measured-on",
        "gates/8.4b-tbc-m910q-2026-09-07/README.md",
        note="level 53 to 60 on an offline character, read back in the row",
    ),
    "wow-tbc:accounts.level.level_column": Provenance(
        "measured-on",
        "gates/8.3b-tbc-m910q-2026-09-07/README.md",
        note="`account_access` asserted ABSENT on this tree, so the level is a column on the row",
    ),
    "wow-tbc:accounts.scheme": Provenance(
        "measured-on",
        "gates/8.3b-tbc-m910q-2026-09-07/README.md",
        note=(
            "the shipped check recomputes the verifier from the row's own salt and clause 2 re-ran "
            "against it"
        ),
    ),
    # -- wow-vanilla (CMaNGOS Classic) — 8.4c and 8.3c on m910q --------------
    "wow-vanilla:play.equipped.template_column": Provenance(
        "measured-on",
        "gates/8.4c-vanilla-m910q-2026-09-07/README.md",
        note="12 of 12 ids resolve in `mangos.item_template`; 0 of the 12 instance guids do",
    ),
    "wow-vanilla:play.teleport_command": Provenance(
        "measured-on",
        "gates/8.4c-vanilla-m910q-2026-09-07/README.md",
        note="`teleport name` closed the connection and the row did not move; `tele name` answered",
    ),
    "wow-vanilla:play.mail_item_cap": Provenance(
        "measured-on",
        "gates/8.4c-vanilla-m910q-2026-09-07/README.md",
        note=(
            "THE box's reason: the 1 had been a source-cited prediction off `Mail.h:49` until "
            "three sends asked"
        ),
    ),
    "wow-vanilla:play.revive_offline": Provenance(
        "measured-on",
        "gates/8.4c-vanilla-m910q-2026-09-07/README.md",
        note=(
            "the CORPSE was watched, not `characters.health` — the column the offline branch does "
            "not touch"
        ),
    ),
    "wow-vanilla:play.rename_command": Provenance(
        "measured-on",
        "gates/8.4c-vanilla-m910q-2026-09-07/README.md",
        note="the prompt at the next login and the renamed character in the world",
    ),
    "wow-vanilla:play.set_level_command": Provenance(
        "measured-on",
        "gates/8.4c-vanilla-m910q-2026-09-07/README.md",
        note="level 53 -> 60 on an offline character",
    ),
    "wow-vanilla:accounts.level.level_column": Provenance(
        "measured-on",
        "gates/8.3c-vanilla-m910q-2026-09-07/README.md",
        note="`SHOW TABLES LIKE 'account_access'` came back EMPTY here",
    ),
    "wow-vanilla:accounts.level.max_level": Provenance(
        "measured-on",
        "gates/8.3c-vanilla-m910q-2026-09-07/README.md",
        note="the SERVER'S OWN help, asked on the running install: `#level may range from 0 to 3`",
    ),
    "wow-vanilla:accounts.scheme": Provenance(
        "measured-on",
        "gates/8.3c-vanilla-m910q-2026-09-07/README.md",
        note="`srpprobe.py`: the SRP6 recipe recomputed by hand and matched against the row",
    ),
    # -- wow-tortoise (Turtle fork) — 8.4d and 8.3d on m910q -----------------
    "wow-tortoise:play.equipped.template_column": Provenance(
        "measured-on",
        "gates/8.4d-tortoise-m910q-2026-09-08/README.md",
        note=(
            "`item_template` as a column of the inventory row, no `item_instance`; 12 of 12 "
            "resolve"
        ),
    ),
    "wow-tortoise:play.teleport_command": Provenance(
        "measured-on",
        "gates/8.4d-tortoise-m910q-2026-09-08/README.md",
        note="`Laureo` (offline) went map 1 -> map 0",
    ),
    "wow-tortoise:play.mail_item_cap": Provenance(
        "measured-on",
        "gates/8.4d-tortoise-m910q-2026-09-08/README.md",
        note="five worn pieces promised five mails and delivered five; `MAX(items per mail)` = 1",
    ),
    "wow-tortoise:play.revive_offline": Provenance(
        "measured-on",
        "gates/8.4d-tortoise-m910q-2026-09-08/README.md",
        note="`Ramoni`'s one corpse watched twelve seconds before the offline revive took it",
    ),
    "wow-tortoise:play.rename_command": Provenance(
        "measured-on",
        "gates/8.4d-tortoise-m910q-2026-09-08/README.md",
        note=(
            "the sibling spelling `character rename` was asked first and answered with a "
            "subcommand list"
        ),
    ),
    "wow-tortoise:play.rename_offline_refusal": Provenance(
        "read-from-source",
        "Commands.cpp:12624-12635",
        note=(
            "the one value here that must NOT be measured: running it would `UPDATE characters "
            "SET name = guid` and throw a real name away. 8.4d proved the ONLINE arm instead "
            "(`Aniel`'s `at_login` 0 -> 1 with the name surviving) and withholds the offline one"
        ),
    ),
    "wow-tortoise:play.set_level_command": Provenance(
        "measured-on",
        "gates/8.4d-tortoise-m910q-2026-09-08/README.md",
        note=(
            "the null: `.rndbot level <bot>` ignored the number it was handed, and `.levelup` is "
            "`AllowConsole` false at `Chat.cpp:923`"
        ),
    ),
    "wow-tortoise:play.set_level_absent_reason": Provenance(
        "measured-on",
        "gates/8.4d-tortoise-m910q-2026-09-08/README.md",
        note=(
            "corrected TWICE against the live console before it was true; the sentence names what "
            "does exist"
        ),
    ),
    "wow-tortoise:accounts.level.level_column": Provenance(
        "measured-on",
        "gates/8.3d-tortoise-m910q-2026-09-07/README.md",
        note=(
            "`rank` moved and `account.security` stayed NULL through every change, both columns "
            "existing"
        ),
    ),
    "wow-tortoise:accounts.level.max_level": Provenance(
        "measured-on",
        "gates/8.3d-tortoise-m910q-2026-09-07/README.md",
        note="asked: `... SHAPROBE 4` accepted, `5` answered `Incorrect values.`",
    ),
}


OWED: dict[str, str] = {
    "wow-wotlk:play.mail_item_cap": (
        "8.4a sent twelve worn items in ONE mail and split nineteen into two, so twelve FITS -- "
        "but nothing on this tree has been asked whether thirteen is refused, and a cap is a "
        "ceiling, not a floor. Its two siblings both have one: 8.4b measured by exceeding it "
        "(`mail-cap.py`, 12 yes / 13 refused) and 8.4c calls an unasked source constant a "
        "prediction. Settled by 8.4b's probe pointed at this box's server."
    ),
    "wow-wotlk:accounts.level.max_level": (
        "Neither 8.3a press probed the ceiling: no refused level appears in either README, on "
        "yulon-ubuntu or on yulon-win11-gate. `AccountLevel.max_level`'s own docstring names "
        "`SEC_ADMINISTRATOR` and gives no `path:line`, so there is not even a `read-from-source` "
        "citation to fall back on. Settled by 8.3d's probe (accept 3, refuse 4) run here."
    ),
    "wow-tbc:accounts.level.max_level": (
        "As wow-wotlk's, and for the same reason: 8.3b changed a level and read it back, and "
        "never asked for one the tree would refuse. The value is right in all likelihood and "
        "that is exactly what makes it worth a row -- a surface drawing the wrong ceiling here "
        "does not fail, it silently offers less than the tree has."
    ),
    "wow-tortoise:accounts.scheme": (
        "`Accounts`' docstring records this measured against a live server on 2026-08-26 -- the "
        "core logging its own INSERT and `SHA1(UPPER(user):UPPER(pass))` matching it exactly -- "
        "and that predates `.notes/gates/`, so no folder in this tree holds the reading. The "
        "measurement is not doubted; the CITATION cannot be resolved, and an unresolvable "
        "citation is the shape `dcc64543` caught in 8.3c's README. Settled by committing the "
        "reading, or by re-running it beside 8.3d's transcript."
    ),
}
"""Values written down about a tree that carry no provenance yet, and why.

This is the set Phase 8's exit line asks to be "enumerated by name in the
catalog test rather than left to memory". Every row says what would settle it,
because a list of debts with no discharge is a list that grows.

It is EXACT and not a floor: `test_the_provenance_debts_are_exactly_these_four`
fails on a key that has since been marked and on a key the catalog no longer
writes, so the list cannot rot in either direction.
"""


def leaves(block: object, prefix: str) -> dict[str, object]:
    """Every value the FILE writes under `block`, keyed by its dotted path.

    Presence in `catalog.json` is the whole rule, and it is the rule because of
    what the two sides mean. A key written down is somebody's claim ABOUT THIS
    TREE -- it was typed, per game, by a person who had a reason -- while a key
    left out takes the model's default, which is a claim about the shape and is
    owned by the field's own docstring in `catalog.py`.

    The rule's known hole, named rather than discovered later: an ABSENT key can
    also be a per-tree claim. `play.revive_offline` is null on `wow-wotlk` and
    `wow-tbc` by being absent, and that null is what withholds the button -- and
    `Play.revive_offline`'s docstring says outright that those two trees measured
    the wrong column and are "the trees whose own boxes have not looked again".
    So two live per-tree claims sit outside this guard's reach by construction.
    The design page carries the alternative (mark every model field, present or
    not) with its cost counted, and leaves the choice to the owner.
    """
    found: dict[str, object] = {}
    if isinstance(block, dict):
        for key, value in block.items():
            found |= leaves(value, f"{prefix}.{key}")
    else:
        found[prefix] = block
    return found
