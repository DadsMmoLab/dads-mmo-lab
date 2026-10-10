"""T558/T555: the world schema at the WotLK pin, and the reader that names a statement's columns.

Shared by `test_wotlk_world_columns.py` (the WotLK manifests' statements) and
`test_unbound_world_sql.py` (the SQL the pinned mod-unbound ships).
"""

from __future__ import annotations

import re

PIN = "2a2211cd8f3d157da432ec0175ddd4b8a191931f"
"""mod-playerbots/azerothcore-wotlk: wow-wotlk's pin, and wow-unbound's core, since T655
(2026-10-10). `PIN_COLUMNS` was read at `FORMER_PINS[0]` and shown to hold here (see `VALID_AT`)."""

FORMER_PINS = (
    "f19a18799a35f7c24bdcdc9ea399c601f166259b",
    "7f12e89ee5f467a50e62eba1d525eac7dc953d03",
)
"""wow-wotlk's earlier pins, newest first: f19a1879 from T389 (2026-10-08, the revision
`PIN_COLUMNS` was read at; wow-unbound's core from T580, 2026-10-09) until T655; 7f12e89e
before that (and the core wow-unbound shipped on)."""

VALID_AT = frozenset({PIN, *FORMER_PINS})
"""The core revisions `PIN_COLUMNS` is known to describe. Between 7f12e89e and f19a1879 no base
`db_world` SQL file changed and the 20 `db_world` updates alter no table (their one DDL is
`CREATE TABLE IF NOT EXISTS` for two new `*_dbc` tables; T389). Between f19a1879 and 2a2211cd
(175 commits) no file under `data/sql/base` changed and the 66 `db_world` updates hold no
ALTER, CREATE, DROP, RENAME or TRUNCATE (the range's one DDL is `db_auth` 2026_09_27_00 on
`uptime`; T655, read in checkouts 2026-10-10). Each pin move must add its revision here after
checking the same two things."""

PIN_COLUMNS: dict[str, frozenset[str]] = {
    "creature": frozenset(
        "guid id map zoneId areaId spawnMask phaseMask equipment_id position_x position_y "
        "position_z orientation spawntimesecs wander_distance currentwaypoint curhealth "
        "curmana MovementType npcflag unit_flags dynamicflags ScriptName VerifiedBuild "
        "CreateObject Comment".split()
    ),
    "gameobject": frozenset(
        "guid id map zoneId areaId spawnMask phaseMask position_x position_y position_z "
        "orientation rotation0 rotation1 rotation2 rotation3 spawntimesecs animprogress state "
        "ScriptName VerifiedBuild Comment".split()
    ),
    "creature_template": frozenset(
        "entry difficulty_entry_1 difficulty_entry_2 difficulty_entry_3 KillCredit1 KillCredit2 "
        "name subname IconName gossip_menu_id minlevel maxlevel exp faction npcflag speed_walk "
        "speed_run speed_swim speed_flight detection_range rank dmgschool DamageModifier "
        "BaseAttackTime RangeAttackTime BaseVariance RangeVariance unit_class unit_flags "
        "unit_flags2 dynamicflags family type type_flags lootid pickpocketloot skinloot "
        "PetSpellDataId VehicleId mingold maxgold AIName MovementType HoverHeight "
        "HealthModifier ManaModifier ArmorModifier ExperienceModifier RacialLeader movementId "
        "RegenHealth CreatureImmunitiesId flags_extra ScriptName VerifiedBuild".split()
    ),
    "creature_template_model": frozenset(
        "CreatureID Idx CreatureDisplayID DisplayScale Probability VerifiedBuild".split()
    ),
    "creature_template_addon": frozenset(
        "entry path_id mount bytes1 bytes2 emote visibilityDistanceType auras".split()
    ),
    "gossip_menu": frozenset("MenuID TextID".split()),
    "gossip_menu_option": frozenset(
        "MenuID OptionID OptionIcon OptionText OptionBroadcastTextID OptionType OptionNpcFlag "
        "ActionMenuID ActionPoiID BoxCoded BoxMoney BoxText BoxBroadcastTextID "
        "VerifiedBuild".split()
    ),
    "npc_text": frozenset(
        "ID text0_0 text0_1 BroadcastTextID0 lang0 Probability0 em0_0 em0_1 em0_2 em0_3 em0_4 "
        "em0_5 text1_0 text1_1 BroadcastTextID1 lang1 Probability1 em1_0 em1_1 em1_2 em1_3 "
        "em1_4 em1_5 text2_0 text2_1 BroadcastTextID2 lang2 Probability2 em2_0 em2_1 em2_2 "
        "em2_3 em2_4 em2_5 text3_0 text3_1 BroadcastTextID3 lang3 Probability3 em3_0 em3_1 "
        "em3_2 em3_3 em3_4 em3_5 text4_0 text4_1 BroadcastTextID4 lang4 Probability4 em4_0 "
        "em4_1 em4_2 em4_3 em4_4 em4_5 text5_0 text5_1 BroadcastTextID5 lang5 Probability5 "
        "em5_0 em5_1 em5_2 em5_3 em5_4 em5_5 text6_0 text6_1 BroadcastTextID6 lang6 "
        "Probability6 em6_0 em6_1 em6_2 em6_3 em6_4 em6_5 text7_0 text7_1 BroadcastTextID7 "
        "lang7 Probability7 em7_0 em7_1 em7_2 em7_3 em7_4 em7_5 VerifiedBuild".split()
    ),
    "smart_scripts": frozenset(
        "entryorguid source_type id link event_type event_phase_mask event_chance event_flags "
        "event_param1 event_param2 event_param3 event_param4 event_param5 event_param6 "
        "action_type action_param1 action_param2 action_param3 action_param4 action_param5 "
        "action_param6 target_type target_param1 target_param2 target_param3 target_param4 "
        "target_x target_y target_z target_o comment".split()
    ),
    "conditions": frozenset(
        "SourceTypeOrReferenceId SourceGroup SourceEntry SourceId ElseGroup "
        "ConditionTypeOrReference ConditionTarget ConditionValue1 ConditionValue2 "
        "ConditionValue3 NegativeCondition ErrorType ErrorTextId ScriptName Comment".split()
    ),
}

TABLE = re.compile(
    r"\b(?:DELETE\s+FROM|UPDATE|INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO|FROM|JOIN)\s+"
    r"(?:`?\w+`?\s*\.\s*)?`?(\w+)`?",
    re.IGNORECASE,
)
"""A table after its keyword; a `schema.` in front of it (`acore_world.creature`) is skipped,
so the table is read as `creature` and not as the schema (T560, cold review of T558)."""
QUALIFIER = re.compile(r"`?\b\w+`?\s*\.\s*(?=`?\w)")
"""`schema.` or `alias.` in front of a name: the qualifier is not a column."""
ALIAS = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+`?\w+`?\s+(?:AS\s+)?`?(\w+)`?", re.IGNORECASE)
WORD = re.compile(r"`([^`]+)`|@?\w+")
PLACEHOLDER = re.compile(r"\{\w+\}")
"""`{ony_level}`, `{hp}`: Yu'lon fills these with the player's answer before the statement runs."""
STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
SQL_WORDS = frozenset("""
    SELECT FROM WHERE AND OR NOT IN IS NULL LIKE BETWEEN EXISTS DELETE UPDATE SET INSERT INTO
    IGNORE REPLACE VALUES ORDER GROUP BY HAVING LIMIT OFFSET ASC DESC AS ON JOIN LEFT RIGHT
    INNER OUTER CROSS USING DISTINCT UNION ALL CASE WHEN THEN ELSE END COUNT SUM MIN MAX AVG
    IF IFNULL COALESCE CONCAT LOWER UPPER TRUE FALSE DUPLICATE KEY DEFAULT INTERVAL
    """.split())
"""SQL's own words. Every other identifier in a statement on a pinned table must be a
column of a table the statement names, or the statement fails: the reader fails closed
on a shape it does not know (Codex adversarial review: a projection, the right side of a
SET, an ORDER BY were not seen by the first reader)."""


def columns_named(statement: str) -> set[str]:
    """Each identifier in `statement` but SQL words, numbers, strings, variables and tables."""
    text = QUALIFIER.sub("", PLACEHOLDER.sub(" 1 ", STRING.sub(" ", statement)))
    tables = {t.lower() for t in TABLE.findall(text)}
    tables |= {
        a.lower() for a in ALIAS.findall(text) if a.upper() not in SQL_WORDS
    }  # `FROM creature c`: `c` is the table, not a column
    named: set[str] = set()
    for m in WORD.finditer(text):
        word = m.group(1) or m.group(0)
        if word.startswith("@") or word.isdigit() or word.upper() in SQL_WORDS:
            continue
        if word.lower() in tables:
            continue
        named.add(word.split(".")[-1])
    return named


def split_statements(text: str) -> list[str]:
    """The statements of a SQL file: split on `;` outside quotes, comments removed.

    Reads `--`, `#` and `/* */` comments and `'…'`/`"…"`/`` `…` `` quotes (a doubled quote or a
    backslash escapes inside), so a `;` or a column-looking word in a comment or a string is
    not taken for SQL. Each statement is stripped; an empty one is dropped.
    """
    out: list[str] = []
    cur: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == "\\" and ch != "`":
                    j += 2
                elif text[j] == ch:
                    if text[j + 1 : j + 2] == ch:
                        j += 2
                    else:
                        break
                else:
                    j += 1
            cur.append(text[i : j + 1])
            i = j + 1
        elif ch == "#" or (
            text.startswith("--", i) and text[i + 2 : i + 3] in (" ", "\t", "\n", "\r", "")
        ):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            cur.append(" ")
        elif ch == ";":
            out.append("".join(cur).strip())
            cur = []
            i += 1
        else:
            cur.append(ch)
            i += 1
    out.append("".join(cur).strip())
    return [s for s in out if s]


def _top_level_groups(text: str) -> list[str]:
    """The text inside each top-level `( … )` of `text`, quotes and nested parens respected."""
    groups: list[str] = []
    depth, start, i, n = 0, 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == "\\" and ch != "`":
                    j += 2
                elif text[j] == ch:
                    if text[j + 1 : j + 2] == ch:
                        j += 2
                    else:
                        break
                else:
                    j += 1
            i = j
        elif ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")":
            depth -= 1
            assert depth >= 0, "unbalanced parenthesis in an INSERT"
            if depth == 0:
                groups.append(text[start:i])
        i += 1
    assert depth == 0, "unbalanced parenthesis in an INSERT"
    return groups


def _split_top_level(text: str) -> list[str]:
    """`text` split on commas outside quotes and parentheses, each piece stripped."""
    pieces: list[str] = []
    depth, start, i, n = 0, 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == "\\" and ch != "`":
                    j += 2
                elif text[j] == ch:
                    if text[j + 1 : j + 2] == ch:
                        j += 2
                    else:
                        break
                else:
                    j += 1
            i = j
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            pieces.append(text[start:i].strip())
            start = i + 1
        i += 1
    pieces.append(text[start:].strip())
    return pieces


def insert_rows(statement: str) -> tuple[list[str], list[list[str]]]:
    """`(columns, rows)` of an `INSERT … INTO t (cols) VALUES (…), (…)`, every value as its text.

    Column names lose their backticks; values are kept as written (a string keeps its quotes).
    Raises AssertionError on an INSERT without an explicit column list or a VALUES clause, so a
    shape this reader does not know is refused rather than guessed at.
    """
    head = re.match(
        r"\s*INSERT\s+(?:IGNORE\s+)?INTO\s+(?:`?\w+`?\s*\.\s*)?`?\w+`?\s*\(", statement, re.I
    )
    assert head, f"not an INSERT with a column list: {statement[:60]}"
    values = re.search(r"\)\s*VALUES\b", statement[head.end() :], re.I)
    assert values, f"an INSERT without VALUES: {statement[:60]}"
    columns_text = statement[head.end() - 1 : head.end() + values.start() + 1]
    (columns_group,) = _top_level_groups(columns_text)
    columns = [c.strip().strip("`") for c in _split_top_level(columns_group)]
    rows = [_split_top_level(g) for g in _top_level_groups(statement[head.end() + values.end() :])]
    return columns, rows
