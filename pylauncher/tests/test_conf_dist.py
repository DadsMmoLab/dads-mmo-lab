"""A module added from outside gets a settings card made from its `.conf.dist` (T590).

A module added by link or folder has its `conf/*.conf.dist` activated with no declared
keys, so the Tuning tab had a raw editor for it and no card. `yulon.conf_dist` reads the
`.conf.dist` at the moment the tab builds its cards and turns each `Key = default` line
into a row. The fixtures below are the shapes of real AzerothCore module `.conf.dist`
files (read 2026-10-09: mod-aoe-loot, mod-solo-lfg, mod-transmog, mod-learn-spells,
mod-npc-beastmaster, mod-1v1-arena, mod-ah-bot, mod-reward-shop, mod-ale, mod-unbound
and mod-dungeon-clear), cut down but not reworded.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from yulon import conf_dist, tuning
from yulon.manifest import Manifest, parse_manifest

MODULES = "env/dist/etc/modules"
CONF = f"{MODULES}/mod_x.conf"

# -- the shapes of real files -----------------------------------------------

# mod-aoe-loot, mod-unbound, mod-solo-lfg: a block above the key, a blank line between.
BLOCK_ABOVE = """\
#
# This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
#

########################################
# AoeLoot module configuration
########################################
#
#    AOELoot.Enable
#        Description: Enables Module
#        Default:     1 - (Enabled)
#                     0 - (Disabled)
#

AOELoot.Enable = 1

#
#    AOELoot.Range
#       Description: Maximum reach range search loot.
#       Default:    55.0
#       Range:      5.0 - 100.0
#

AOELoot.Range = 55.0

#
#    AOELoot.MailEnable
#        Description: Mail an item looted from an AoE loot window when it
#                     does not fit in the bags. Unique items and quest items
#                     auto-collected from nearby corpses are never mailed.
#        Default:     0 - (Disabled)
#

AOELoot.MailEnable = 0
"""

# mod-learn-spells, mod-npc-beastmaster: plain prose lines above the key.
PLAIN_COMMENTS = """\
[worldserver]

###################################################################################################
#	Learn spells on level-up
###################################################################################################

# Enable the module? (1: true | 0: false)

LearnSpells.Enable = 1

# Max level Limit the player will learn spells
# 	Default:  = 80

LearnSpells.MaxLevel = 80

# Custom login/help message (leave empty for default)
BeastMaster.LoginMessage = ""
# Required minimum level to adopt pets. (default: 10, 0 = disable)
BeastMaster.MinLevel = 10
"""

# mod-transmog, mod-cfbg: every key is described in one block at the top, and the
# `Key = value` lines come later, far from their description.
HEADER_DOCS = """\
[worldserver]

###################################################################################################
#    Transmogrification config
###################################################################################################
#
#    SETTINGS
#
#    Transmogrification.Enable
#        Description: Enables/Disables transmog.
#                     Players won't be able to see any transmogrified item while disabled.
#        Default:     1
#
#    Transmogrification.AllowRare
#        Description: Allow rare quality items to be used as source and target items
#        Default:    1
#
###################################################################################################

Transmogrification.Enable = 1
Transmogrification.AllowRare = 1
Transmogrification.Unmentioned = 7
"""

# mod-ah-bot: the description has no label and the default has no colon.
AHBOT = """\
[worldserver]

###############################################################################
# AUCTION HOUSE BOT SETTINGS
#
#    AuctionHouseBot.DEBUG
#        Enable/Disable Debugging output
#    Default 0 (disabled)
#
#    AuctionHouseBot.UseBuyPriceForSeller
#        Should the Seller use BuyPrice or SellPrice to determine Bid Prices
#    Default 0 (use SellPrice)
#
###############################################################################

AuctionHouseBot.DEBUG = 0
AuctionHouseBot.UseBuyPriceForSeller = 0
"""

# mod-1v1-arena: no blank line between the block and the key, and a header whose name
# the module spelled differently from the key under it.
NO_BLANKS = """\
[worldserver]
###################################################################################################
# 1V1 ARENA CONFIG
#
#     Arena.1v1.Enable
#         Description: Enable the 1v1 arena.
#         Default:     0 - (Disabled)
#                      1 - (Enabled)
Arena1v1.Enable = 1
#    Arena1v1.MinLevel
#        Description: Min level to create an arena team
#        Default:     80
Arena1v1.MinLevel = 80
"""

# mod-dungeon-clear: free prose under the key's name, ranges spelled in the prose.
FREE_PROSE = """\
[worldserver]
########################################
# GENERAL SETTINGS
########################################

#
#    DungeonClear.PreventBotRelease
#        1 (default): dead bots stay as a corpse until resurrected instead of
#        auto-releasing to the graveyard. 0 restores stock auto-release.
#
#        Default: 1
#

DungeonClear.PreventBotRelease = 1

#
#    DungeonClear.LootMinQuality
#        Minimum item rarity worth looting.
#            0 = Poor/grey (loot everything)
#            1 = Common (white)   2 = Uncommon (green)
#            3 = Rare (blue)      4 = Epic (purple)
#
#        Default: 0
#

DungeonClear.LootMinQuality = 0
"""

# mod-reward-shop, mod-ale: a quoted text value; a true/false default.
QUOTED = """\
[worldserver]
#
#    WebsiteAddress
#        Description: This is what the NPC says when a player clicks "How do i get a code?"
#        Default:     "You can get codes by visiting the online store"
WebsiteAddress = "You can get codes by visiting the online store"
#
#    ALE.Enabled
#        Description: Enable or disable ALE LuaEngine
#        Default:    true  - (enabled)
#                    false - (disabled)
ALE.Enabled = true
"""


def _by_key(text: str) -> dict[str, conf_dist.DistKey]:
    return {item.key: item for item in conf_dist.parse(text)}


# -- parse: which lines are keys -------------------------------------------


def test_every_active_assignment_is_a_row_in_the_files_order() -> None:
    keys = [item.key for item in conf_dist.parse(BLOCK_ABOVE)]
    assert keys == ["AOELoot.Enable", "AOELoot.Range", "AOELoot.MailEnable"]


def test_a_commented_out_assignment_and_a_section_header_are_not_rows() -> None:
    text = "[worldserver]\n#Old.Key = 4\n  # Other = 5\nReal.Key = 1\n"
    assert [item.key for item in conf_dist.parse(text)] == ["Real.Key"]


def test_a_key_the_file_assigns_twice_is_one_row_with_the_first_value() -> None:
    """The core keeps the FIRST copy of a key (`tuning.conf_value`), so does the row."""
    (item,) = conf_dist.parse("A.B = 1\nA.B = 2\n")
    assert item.default == "1"


def test_a_crlf_file_reads_the_same_as_an_lf_one() -> None:
    plain = conf_dist.parse(BLOCK_ABOVE)
    crlf = conf_dist.parse(BLOCK_ABOVE.replace("\n", "\r\n"))
    assert crlf == plain


def test_a_dist_with_no_assignment_has_no_rows() -> None:
    assert conf_dist.parse("# only prose\n\n[worldserver]\n") == ()


def test_the_default_is_the_value_the_dist_assigns_without_its_quotes() -> None:
    found = _by_key(QUOTED)
    assert found["WebsiteAddress"].default == "You can get codes by visiting the online store"


# -- parse: the help text ---------------------------------------------------


def test_block_above_style_help_is_the_description_without_the_default_lines() -> None:
    found = _by_key(BLOCK_ABOVE)
    assert found["AOELoot.Enable"].help == "Enables Module"
    assert found["AOELoot.MailEnable"].help == (
        "Mail an item looted from an AoE loot window when it does not fit in the bags. "
        "Unique items and quest items auto-collected from nearby corpses are never mailed."
    )


def test_a_range_the_comment_states_stays_in_the_help() -> None:
    assert _by_key(BLOCK_ABOVE)["AOELoot.Range"].help == (
        "Maximum reach range search loot. Range: 5.0 - 100.0"
    )


def test_the_files_licence_and_title_banner_are_not_a_keys_help() -> None:
    """The banner at the top is a block too, and the first key must not inherit it."""
    help_text = _by_key(BLOCK_ABOVE)["AOELoot.Enable"].help
    assert help_text is not None
    assert "AzerothCore Project" not in help_text and "module configuration" not in help_text


def test_plain_prose_above_the_key_is_its_help() -> None:
    found = _by_key(PLAIN_COMMENTS)
    assert found["LearnSpells.Enable"].help == "Enable the module? (1: true | 0: false)"
    assert (
        found["BeastMaster.LoginMessage"].help
        == "Custom login/help message (leave empty for default)"
    )
    assert found["BeastMaster.MinLevel"].help == (
        "Required minimum level to adopt pets. (default: 10, 0 = disable)"
    )


def test_a_section_title_is_not_help() -> None:
    text = "###\n# Learn spells on level-up\n###\n\nA.B = 1\n"
    assert _by_key(text)["A.B"].help is None


def test_help_described_in_a_block_far_above_is_found_by_the_keys_name() -> None:
    found = _by_key(HEADER_DOCS)
    assert found["Transmogrification.Enable"].help == (
        "Enables/Disables transmog. Players won't be able to see any transmogrified "
        "item while disabled."
    )
    assert found["Transmogrification.AllowRare"].help == (
        "Allow rare quality items to be used as source and target items"
    )


def test_a_key_the_header_does_not_mention_has_no_help() -> None:
    assert _by_key(HEADER_DOCS)["Transmogrification.Unmentioned"].help is None


def test_an_unlabelled_description_and_a_default_with_no_colon() -> None:
    found = _by_key(AHBOT)
    assert found["AuctionHouseBot.DEBUG"].help == "Enable/Disable Debugging output"
    assert found["AuctionHouseBot.UseBuyPriceForSeller"].help == (
        "Should the Seller use BuyPrice or SellPrice to determine Bid Prices"
    )


def test_no_blank_line_between_the_block_and_its_key() -> None:
    found = _by_key(NO_BLANKS)
    assert found["Arena1v1.MinLevel"].help == "Min level to create an arena team"
    # The header says `Arena.1v1.Enable`; the one entry right above is still the key's.
    assert found["Arena1v1.Enable"].help == "Enable the 1v1 arena."


def test_prose_with_no_label_is_the_help() -> None:
    found = _by_key(FREE_PROSE)
    assert found["DungeonClear.PreventBotRelease"].help == (
        "1 (default): dead bots stay as a corpse until resurrected instead of "
        "auto-releasing to the graveyard. 0 restores stock auto-release."
    )


def test_a_blocks_help_goes_to_one_key_only() -> None:
    text = "# What both do\nA.B = 1\nC.D = 2\n"
    found = _by_key(text)
    assert found["A.B"].help == "What both do"
    assert found["C.D"].help is None


def test_a_very_long_help_is_cut_at_a_word() -> None:
    word = "alpha "
    text = f"# {word * 400}\nA.B = 1\n"
    help_text = _by_key(text)["A.B"].help
    assert help_text is not None
    assert len(help_text) <= conf_dist.HELP_LIMIT + 1
    assert help_text.endswith("…") and not help_text.endswith("alph…")


# -- parse: the type, only where the default makes it certain ---------------


def test_a_zero_or_one_default_is_a_switch() -> None:
    found = _by_key(BLOCK_ABOVE)
    assert found["AOELoot.Enable"].type == "bool"
    assert found["AOELoot.MailEnable"].type == "bool"


def test_a_whole_number_default_is_a_number() -> None:
    assert _by_key(PLAIN_COMMENTS)["LearnSpells.MaxLevel"].type == "int"
    assert _by_key(NO_BLANKS)["Arena1v1.MinLevel"].type == "int"


def test_a_zero_or_one_default_whose_comment_lists_other_numbers_is_a_number_not_a_switch() -> None:
    """`DungeonClear.LootMinQuality = 0` takes 0 to 4: a switch would offer two of five values."""
    found = _by_key(FREE_PROSE)
    assert found["DungeonClear.LootMinQuality"].type == "int"
    assert found["DungeonClear.PreventBotRelease"].type == "bool"


def test_a_zero_or_one_default_with_nothing_saying_it_is_a_toggle_is_a_number() -> None:
    """mod-ah-bot's `AuctionHouseBot.GUID = 0` and mod-ale's `ALE.AutoReloadInterval = 1`.

    A default of 0 or 1 does not make a key a switch: one is an id, the other seconds.
    """
    text = (
        "# Sets the interval in seconds between checks. Lower values are faster.\n"
        "ALE.AutoReloadInterval = 1\n"
        "AuctionHouseBot.GUID = 0\n"
        "# How many bots to keep, 0 for none and 1 for one\n"
        "Bots.Kept = 1\n"
    )
    found = _by_key(text)
    assert found["ALE.AutoReloadInterval"].type == "int"
    assert found["AuctionHouseBot.GUID"].type == "int"
    assert found["Bots.Kept"].type == "int", "a count that happens to be 0 or 1 is a number"


@pytest.mark.parametrize(
    ("key", "words"),
    [
        ("Mod.Thing", "Enables the thing. 1 - (Enabled) 0 - (Disabled)"),
        ("Mod.Thing", "Turn it on or off"),
        ("Mod.Thing", "Whether bots wait (1: yes | 0: no)"),
        ("Mod.Thing", "1 stays as a corpse. 0 restores stock behaviour."),
        ("Mod.EnableThing", ""),
        ("Mod.DisableThing", ""),
        ("Mod.AllowThing", ""),
    ],
)
def test_what_makes_a_zero_or_one_default_a_switch(key: str, words: str) -> None:
    comment = f"# {words}\n" if words else ""
    assert _by_key(f"{comment}{key} = 1\n")[key].type == "bool"


def test_a_debug_or_trace_name_alone_does_not_make_a_switch() -> None:
    """Codex review: `DebugLevel = 1` may take 2, so the name `Debug` proves nothing."""
    found = _by_key("Mod.DebugLevel = 1\nMod.TraceOutput = 0\n")
    assert [item.type for item in found.values()] == ["int", "int"]


@pytest.mark.parametrize(
    ("name", "text", "default"),
    [
        (
            "a key whose options sit under Default:",
            "#    Mod.Difficulty\n"
            "#        Description: Enable a harder difficulty for dungeons.\n"
            "#        Default:     0 - (Disabled)\n"
            "#                     1 - (Heroic)\n"
            "#                     2 - (Mythic)\n"
            "Mod.Difficulty = 0\n",
            "0",
        ),
        (
            "a decimal third value under Default:",
            "#    Mod.Mode\n"
            "#        Description: Enable the module.\n"
            "#        Default:     0 - Disabled\n"
            "#                     1 - Normal\n"
            "#                     2.5 - Aggressive\n"
            "Mod.Mode = 0\n",
            "0",
        ),  # a number box would refuse the 2.5: text
        (
            "a hexadecimal third value under Default:",
            "#    Mod.Flags\n"
            "#        Description: Enable the module.\n"
            "#        Default:     0 - Disabled\n"
            "#                     1 - Normal\n"
            "#                     0x2 - Aggressive\n"
            "Mod.Flags = 1\n",
            "1",
        ),
        (
            "`on` in a sentence is not a switch",
            "# Item entry given to the player on login\nMod.LoginItem = 0\n",
            "0",
        ),
        (
            "a name that says Announce is not a switch",
            "# Chat channel id the module announces in\nMod.Announce = 1\n",
            "1",
        ),
        (
            "`no` in a sentence is not a switch",
            "# Faction template for the NPC. 0 means no change\nMod.Faction = 0\n",
            "0",
        ),
    ],
)
def test_a_key_with_more_than_two_values_or_only_everyday_words_is_not_a_switch(
    name: str, text: str, default: str
) -> None:
    """Cold review: the options under `Default:` and the words `on`/`no` made these switches.

    A switch that is wrong loses the user's value (a server at 2 reads as on, and turning it
    on writes 1); a number box is always safe.
    """
    (item,) = conf_dist.parse(text)
    decimal = "2.5" in text
    assert (item.default, item.type) == (default, None if decimal else "int"), name


def test_a_negative_value_shown_only_under_default_makes_the_key_text() -> None:
    """Codex review: `-1 - (Unlimited)` under `Default:` is a signed range the help leaves out.

    The number check would read the key as a uint32 and refuse the -1 the module takes; a text
    box refuses nothing. A key whose negative sits in the description stays a signed number.
    """
    text = (
        "#    Mod.Cap\n"
        "#        Description: Most items.\n"
        "#        Default:     0\n"
        "#                     -1 - (Unlimited)\n"
        "Mod.Cap = 0\n"
        "# 0 for off, -1 for no limit\n"
        "Mod.Limit = 0\n"
        "# 0 picks the first, -1 a random one\n"
        "Mod.Pick = 0\n"
    )
    found = _by_key(text)
    assert found["Mod.Cap"].type is None
    assert found["Mod.Limit"].type == "int"
    assert found["Mod.Pick"].type == "int", "`-1` is a third value, not a `1`: not a switch"


def test_a_decimal_default_is_text() -> None:
    assert _by_key(BLOCK_ABOVE)["AOELoot.Range"].type is None


def test_a_word_default_is_text() -> None:
    found = _by_key(QUOTED)
    assert found["WebsiteAddress"].type is None
    assert found["ALE.Enabled"].type is None


def test_a_whole_number_default_whose_comment_talks_of_decimals_is_text() -> None:
    """`Rate = 1` may take 1.5: a number box that refused it would block a value it reads."""
    text = "# Multiplier for gold, for example 1.5\nGold.Rate = 1\n"
    assert _by_key(text)["Gold.Rate"].type is None
    assert _by_key("# Gold rate multiplier\nGold.Rate = 2\n")["Gold.Rate"].type is None


def test_a_default_past_a_32_bit_number_is_text() -> None:
    assert _by_key("Big.Number = 4294967296\n")["Big.Number"].type is None
    assert _by_key("Small.Number = 2147483647\n")["Small.Number"].type == "int"
    assert _by_key("Low.Number = -2147483649\n")["Low.Number"].type is None


def test_a_number_key_accepts_the_whole_uint32_range_unless_its_comment_shows_a_negative() -> None:
    """Codex review: `AuctionHouseBot.GUID = 0` is read as a uint32, so 3000000000 is legitimate.

    A default of 0 or above says nothing about signedness, so the number check must not cap
    at the signed 32-bit largest; a comment that shows a negative value means signed.
    """
    text = (
        "AuctionHouseBot.GUID = 0\n"
        "# 0 for off, -1 for no limit\n"
        "Cap.Items = 0\n"
        "Level.Offset = -3\n"
        "Big.Id = 4000000000\n"
    )
    found = _by_key(text)
    assert [found[k].type for k in found] == ["int", "int", "int", "int"]
    assert found["Big.Id"].default == "4000000000"
    rows = tuple(
        tuning.TuningRow(
            module_id="mod-x",
            module_name="Mod X",
            family="module",
            file=CONF,
            key=item.key,
            label=item.key,
            explain=item.help,
            type=item.type,
            min=None,
            max=None,
            default=item.default,
            current=None,
            installed=True,
            backend="conf",
            read_only_reason=None,
        )
        for item in found.values()
    )
    spec = conf_dist.conf_keys(rows, "module", "mod-x", CONF)
    tuning.check(spec["AuctionHouseBot.GUID"], "3000000000")
    with pytest.raises(tuning.TuningError):
        tuning.check(spec["AuctionHouseBot.GUID"], "4294967296")
    with pytest.raises(tuning.TuningError):
        tuning.check(spec["AuctionHouseBot.GUID"], "-1")
    tuning.check(spec["Cap.Items"], "-1")
    with pytest.raises(tuning.TuningError):
        tuning.check(spec["Cap.Items"], "2147483648")
    tuning.check(spec["Level.Offset"], "-9")
    tuning.check(spec["Big.Id"], "4294967295")


def test_a_default_with_a_sign_or_leading_zero_is_not_read_as_a_switch() -> None:
    assert _by_key("A.B = 01\n")["A.B"].type is None
    assert _by_key("A.B = -1\n")["A.B"].type == "int"


# -- the rows ---------------------------------------------------------------


def _manifest(**over: Any) -> Manifest:
    base: dict[str, Any] = {
        "schema_version": 1,
        "id": "mod-x",
        "name": "Mod X",
        "type": "module",
        "game": "wow-wotlk",
        "description": "A module added from outside.",
        "source": {"repo": "someone/mod-x"},
        "conf": [{"file": CONF, "template": "conf/mod_x.conf.dist", "keys": []}],
    }
    return parse_manifest({**base, **over})


def _put(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _rows(
    root: Path,
    manifests: list[Manifest] | None = None,
    *,
    core: tuple[str, ...] = (),
    declared: tuple[str, ...] = (),
) -> tuple[tuning.TuningRow, ...]:
    return conf_dist.rows_for(
        manifests if manifests is not None else [_manifest()],
        {"module": frozenset({"mod-x"})},
        root,
        core_files=core,
        declared_files=declared,
        clone_dir=lambda manifest: root / "modules" / manifest.id,
    )


DIST = '# Is it on?\nMod.Enable = 1\n# Level\nMod.Level = 5\nMod.Name = "abc"\n'


def test_a_module_conf_with_no_declared_keys_gets_a_row_per_dist_key(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Mod.Enable = 0\nMod.Level = 9\n")
    _put(tmp_path, f"{CONF}.dist", DIST)
    rows = _rows(tmp_path)
    assert [r.key for r in rows] == ["Mod.Enable", "Mod.Level", "Mod.Name"]
    first = rows[0]
    assert (first.module_id, first.module_name, first.family) == ("mod-x", "Mod X", "module")
    assert first.file == CONF
    assert (first.label, first.explain, first.type, first.default) == (
        "Mod.Enable",
        "Is it on?",
        "bool",
        "1",
    )
    assert (first.min, first.max, first.backend, first.installed) == (None, None, "conf", True)
    assert first.read_only_reason is None and first.editable
    assert first.current == "0"
    assert rows[1].current == "9"
    assert rows[2].current is None, "the .conf has no Mod.Name: current is not invented"


def test_the_dist_beside_the_conf_wins_over_the_clones_template(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Mod.Enable = 0\n")
    _put(tmp_path, f"{CONF}.dist", "Beside.Key = 1\n")
    _put(tmp_path, "modules/mod-x/conf/mod_x.conf.dist", "Clone.Key = 1\n")
    assert [r.key for r in _rows(tmp_path)] == ["Beside.Key"]


def test_with_no_dist_beside_it_the_clones_template_is_read(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Clone.Key = 3\n")
    _put(tmp_path, "modules/mod-x/conf/mod_x.conf.dist", "# From the clone\nClone.Key = 1\n")
    (row,) = _rows(tmp_path)
    assert (row.key, row.explain, row.current) == ("Clone.Key", "From the clone", "3")


@pytest.mark.parametrize(
    "template",
    ["../other/mod_x.conf.dist", "conf/../../other.conf.dist", "..\\other\\x.conf.dist", "{abs}"],
)
def test_a_template_that_leaves_the_clone_is_not_read(tmp_path: Path, template: str) -> None:
    """Codex review: a template path is the clone's own, in either separator style."""
    _put(tmp_path, CONF, "Other.Key = 1\n")
    # The clone exists: on Linux a `..` through a missing folder resolves to nothing, which
    # would refuse the path with or without the guard (cold review).
    (tmp_path / "modules/mod-x/conf").mkdir(parents=True)
    # On Linux a backslash is part of a file's name, so the same spelling is also a plain
    # file in the clone: only the separator rule refuses it.
    _put(tmp_path, "modules/mod-x/..\\other\\x.conf.dist", "Other.Key = 1\n")
    _put(tmp_path, "modules/other/mod_x.conf.dist", "Other.Key = 1\n")
    _put(tmp_path, "modules/other.conf.dist", "Other.Key = 1\n")
    template = template.replace("{abs}", str(tmp_path / "modules/other.conf.dist"))
    manifest = _manifest(conf=[{"file": CONF, "template": template, "keys": []}])
    assert _rows(tmp_path, [manifest]) == ()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_template_that_is_a_link_out_of_the_clone_is_not_read(tmp_path: Path) -> None:
    """A link in the clone to a file elsewhere in the server folder (`worldserver.conf.dist`)."""
    _put(tmp_path, CONF, "Other.Key = 1\n")
    _put(tmp_path, "env/dist/etc/worldserver.conf.dist", "Other.Key = 1\n")
    clone = tmp_path / "modules/mod-x/conf"
    clone.mkdir(parents=True)
    (clone / "mod_x.conf.dist").symlink_to(tmp_path / "env/dist/etc/worldserver.conf.dist")
    assert _rows(tmp_path) == ()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_template_under_a_link_out_of_the_clone_is_not_read(tmp_path: Path) -> None:
    """The folder `conf` in the clone is a link to another folder of the server."""
    _put(tmp_path, CONF, "Other.Key = 1\n")
    _put(tmp_path, "env/dist/etc/mod_x.conf.dist", "Other.Key = 1\n")
    (tmp_path / "modules/mod-x").mkdir(parents=True)
    (tmp_path / "modules/mod-x/conf").symlink_to(
        tmp_path / "env/dist/etc", target_is_directory=True
    )
    assert _rows(tmp_path) == ()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_template_that_is_a_link_inside_the_clone_is_read(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Clone.Key = 1\n")
    _put(tmp_path, "modules/mod-x/data/real.dist", "Clone.Key = 1\n")
    (tmp_path / "modules/mod-x/conf").mkdir()
    (tmp_path / "modules/mod-x/conf/mod_x.conf.dist").symlink_to(
        tmp_path / "modules/mod-x/data/real.dist"
    )
    assert [r.key for r in _rows(tmp_path)] == ["Clone.Key"]


def test_no_dist_anywhere_is_no_card(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Mod.Enable = 0\n")
    assert _rows(tmp_path) == ()


def test_a_dist_with_no_keys_falls_back_to_the_clones_template(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Clone.Key = 1\n")
    _put(tmp_path, f"{CONF}.dist", "# nothing here\n")
    _put(tmp_path, "modules/mod-x/conf/mod_x.conf.dist", "Clone.Key = 1\n")
    assert [r.key for r in _rows(tmp_path)] == ["Clone.Key"]


def test_a_conf_that_is_not_on_disk_yet_has_no_card(tmp_path: Path) -> None:
    """A Save must never create the module's conf: the install lays it."""
    _put(tmp_path, f"{CONF}.dist", DIST)
    assert _rows(tmp_path) == ()


def test_a_module_that_is_not_installed_has_no_card(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Mod.Enable = 0\n")
    _put(tmp_path, f"{CONF}.dist", DIST)
    rows = conf_dist.rows_for(
        [_manifest()],
        {"module": frozenset()},
        tmp_path,
        core_files=(),
        declared_files=(),
        clone_dir=lambda manifest: tmp_path / "modules" / manifest.id,
    )
    assert rows == ()


def test_a_conf_whose_keys_the_manifest_declares_is_left_to_its_own_card(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Mod.Enable = 0\n")
    _put(tmp_path, f"{CONF}.dist", DIST)
    declared = _manifest(conf=[{"file": CONF, "keys": [{"key": "Mod.Enable"}]}])
    assert _rows(tmp_path, [declared]) == ()


def test_a_file_another_card_already_declares_keys_for_gets_no_second_card(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Mod.Enable = 0\n")
    _put(tmp_path, f"{CONF}.dist", DIST)
    assert _rows(tmp_path, declared=(CONF,)) == ()


def test_the_servers_own_conf_never_gets_a_card(tmp_path: Path) -> None:
    own = f"{MODULES}/playerbots.conf"
    _put(tmp_path, own, "A.B = 0\n")
    _put(tmp_path, f"{own}.dist", "A.B = 0\n")
    manifest = _manifest(conf=[{"file": own, "template": "conf/playerbots.conf.dist", "keys": []}])
    assert _rows(tmp_path, [manifest], core=(own,)) == ()
    assert len(_rows(tmp_path, [manifest], core=())) == 1


@pytest.mark.parametrize(
    "file",
    [
        "env/dist/etc/worldserver.conf",
        f"{MODULES}/sub/x.conf",
        f"{MODULES}/*.conf",
        f"{MODULES}/script.lua",
        "../outside.conf",
    ],
)
def test_only_a_conf_directly_in_the_modules_folder_can_get_a_card(
    tmp_path: Path, file: str
) -> None:
    _put(tmp_path, file, "A.B = 0\n")
    _put(tmp_path, f"{file}.dist", "A.B = 0\n")
    manifest = _manifest(conf=[{"file": file, "template": "conf/x.conf.dist", "keys": []}])
    assert _rows(tmp_path, [manifest]) == ()


def test_two_keyless_confs_of_one_module_are_one_card_with_two_files(tmp_path: Path) -> None:
    second = f"{MODULES}/mod_y.conf"
    for name in (CONF, second):
        _put(tmp_path, name, "A.B = 1\n")
        _put(tmp_path, f"{name}.dist", "A.B = 1\n")
    manifest = _manifest(
        conf=[
            {"file": CONF, "template": "conf/mod_x.conf.dist", "keys": []},
            {"file": second, "template": "conf/mod_y.conf.dist", "keys": []},
        ]
    )
    rows = _rows(tmp_path, [manifest])
    assert [(r.module_id, r.file) for r in rows] == [("mod-x", CONF), ("mod-x", second)]


def test_a_conf_that_is_not_utf8_has_no_card(tmp_path: Path) -> None:
    path = _put(tmp_path, CONF, "")
    path.write_bytes(b"A.B = \xff\xfe\n")
    _put(tmp_path, f"{CONF}.dist", "A.B = 1\n")
    assert _rows(tmp_path) == ()


def test_a_dist_that_is_not_utf8_is_not_read(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "A.B = 1\n")
    (tmp_path / f"{CONF}.dist").write_bytes(b"A.B = \xff\n")
    assert _rows(tmp_path) == ()


def test_a_huge_dist_is_not_read(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "A.B = 1\n")
    _put(tmp_path, f"{CONF}.dist", "A.B = 1\n" + "# x\n" * tuning.MAX_EDIT_BYTES)
    assert _rows(tmp_path) == ()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_dist_that_is_a_link_out_of_the_server_folder_is_not_read(tmp_path: Path) -> None:
    server = tmp_path / "server"
    other = tmp_path / "elsewhere.dist"
    other.write_text("Secret.Key = 1\n", encoding="utf-8")
    _put(server, CONF, "A.B = 1\n")
    (server / f"{CONF}.dist").symlink_to(other)
    assert _rows(server) == ()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_conf_that_is_a_link_out_of_the_server_folder_has_no_card(tmp_path: Path) -> None:
    server = tmp_path / "server"
    other = tmp_path / "elsewhere.conf"
    other.write_text("A.B = 1\n", encoding="utf-8")
    (server / MODULES).mkdir(parents=True)
    (server / CONF).symlink_to(other)
    _put(server, f"{CONF}.dist", "A.B = 1\n")
    assert _rows(server) == ()


def test_a_key_that_is_not_one_plain_key_is_read_only(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "A.B = 1\n")
    _put(tmp_path, f"{CONF}.dist", "A.B = 1\nWeird Key (x) = 2\n")
    rows = {r.key: r for r in _rows(tmp_path)}
    assert rows["A.B"].editable
    assert not rows["Weird Key (x)"].editable


# -- what the save checks ---------------------------------------------------


def test_the_specs_carry_each_rows_type_so_a_bad_value_is_refused(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Mod.Enable = 0\nMod.Level = 9\n")
    _put(tmp_path, f"{CONF}.dist", DIST)
    spec = conf_dist.conf_keys(_rows(tmp_path), "module", "mod-x", CONF)
    assert set(spec) == {"Mod.Enable", "Mod.Level", "Mod.Name"}
    assert spec["Mod.Enable"].type == "bool" and spec["Mod.Level"].type == "int"
    assert spec["Mod.Name"].type is None
    with pytest.raises(tuning.TuningError):
        tuning.check(spec["Mod.Level"], "lots")
    with pytest.raises(tuning.TuningError):
        tuning.check(spec["Mod.Enable"], "2")
    tuning.check(spec["Mod.Name"], "anything at all")


def test_the_specs_of_another_module_or_file_are_not_returned(tmp_path: Path) -> None:
    _put(tmp_path, CONF, "Mod.Enable = 0\n")
    _put(tmp_path, f"{CONF}.dist", DIST)
    rows = _rows(tmp_path)
    assert conf_dist.conf_keys(rows, "module", "mod-other", CONF) == {}
    assert conf_dist.conf_keys(rows, "ale", "mod-x", CONF) == {}
    assert conf_dist.conf_keys(rows, "module", "mod-x", f"{MODULES}/other.conf") == {}


def test_a_change_through_the_tuning_writer_moves_only_that_line(tmp_path: Path) -> None:
    """The card's save is `tuning.write` with the rows' specs: a byte-for-byte edit and a backup."""
    original = "# keep me\r\n[worldserver]\r\nMod.Enable = 0\r\n\r\nMod.Level = 9\r\n# tail"
    path = _put(tmp_path, CONF, original)
    _put(tmp_path, f"{CONF}.dist", DIST)
    spec = conf_dist.conf_keys(_rows(tmp_path), "module", "mod-x", CONF)
    made = tuning.write(
        path, {"Mod.Level": "12"}, spec=spec, now=datetime(2026, 10, 9, 12, 0), root=tmp_path
    )
    assert path.read_bytes() == original.replace("Mod.Level = 9", "Mod.Level = 12").encode()
    assert made.read_bytes() == original.encode()
