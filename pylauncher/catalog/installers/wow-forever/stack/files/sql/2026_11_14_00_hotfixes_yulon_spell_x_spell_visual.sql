-- Yu'lon: SpellXSpellVisual rows the 70888 client cannot read, sent to it as hotfixes.
--
-- The server sends a SpellXSpellVisual id in SMSG_SPELL_GO and the client resolves it in its own data.
-- These 18 rows are in the 70707 client data but not readable in 70888 (official push 113139 removes
-- them; their replacements are in sections locked by TACT keys the official server sends and we do not
-- have). A realm extracted from 70888 has no row for these spells and sends visual 0; one extracted from
-- 70707 sends ids the 70888 client cannot resolve. Either way Holy Strike, Blessing of Light, Salvation,
-- Mutilate and Revoke play no animation. Each row's visual and its events exist in the 70888 client.
-- Values are the 70707 rows, field for field. Push 3100001 is Yu'lon's own (never official, never
-- Karrz's 2000101), so a client that cached official pushes still fetches it. Proven in game on Steam
-- Deck #1 on 2026-11-14 (Holy Strike rank 1 animates).
REPLACE INTO `spell_x_spell_visual` VALUES (236732,0,190305,1.0,0,1,0,0,0,0,0,0,678,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,236732,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (236733,0,190305,1.0,0,1,0,0,0,0,0,0,679,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,236733,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (236986,0,190305,1.0,0,1,0,0,0,0,0,0,680,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,236986,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (237167,0,190305,1.0,0,1,0,0,0,0,0,0,1866,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,237167,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (237776,0,300,1.0,0,1,0,0,0,0,0,0,1038,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,237776,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (238199,0,190305,1.0,0,1,0,0,0,0,0,0,2495,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,238199,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (238251,0,107,1.0,0,1,0,0,0,0,0,0,1912,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,238251,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (240549,0,190305,1.0,0,1,0,0,0,0,0,0,5569,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,240549,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (243067,0,190305,1.0,0,1,0,0,0,0,0,0,10332,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,243067,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (243237,0,190305,1.0,0,1,0,0,0,0,0,0,10333,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,243237,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (246969,0,300,1.0,0,1,0,0,0,0,0,0,19977,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,246969,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (247146,0,300,1.0,0,1,0,0,0,0,0,0,19979,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,247146,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (247517,0,300,1.0,0,1,0,0,0,0,0,0,19978,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,247517,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (250245,0,300,1.0,0,1,0,0,0,0,0,0,25895,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,250245,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (250735,0,300,1.0,0,1,0,0,0,0,0,0,25890,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,250735,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (529253,0,7913,1.0,0,1,0,0,0,0,0,0,1310707,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,529253,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (534600,0,192456,1.0,0,0,0,0,0,0,0,0,1319623,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,534600,1,70888);
REPLACE INTO `spell_x_spell_visual` VALUES (538320,0,194926,1.0,0,1,0,0,0,0,0,0,1325499,70888);
REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,538320,1,70888);
