"""T145: a Tuning restore can clear the random-bot rebuild request, but never arm one.

T144's rebuild request is `AiPlayerbot.RandomBotPoolReset = once:<token>` in
Tortoise's `aiplayerbot.conf`, and the Maintenance restore sets a pending one to
`off` before it loads a backup, because the restored characters database carries
no record of that rebuild. The hole: a request pending while the Playerbots card
saved something (the bot count) is IN that save's backup, and the card's Revert
put the newest backup back whole -- `once:<token>` included -- so the next start
deleted the restored bots. Every route that writes a Tuning backup's content back
(the card's Revert, the raw editor's Revert, Undo the last reset) now keeps the
key as the file on disk has it whenever the backup's value asks for a rebuild the
file does not ask for now. Every other key comes back exactly as before.

The second half is the reader: the module reads `aiplayerbot.conf` through ACE's
ini importer (`Config::Reload` -> `ACE_Ini_ImpExp::import_config`, ACE 7.1.2, the
Ubuntu 24.04 `libace-dev` the Tortoise image builds against): each line trimmed,
`;` or `#` a comment, the name and the value split at the first `=` and trimmed,
one pair of surrounding double quotes stripped, and a later assignment replacing
an earlier one (`set_string_value`). `poolreset` now reads, and writes, exactly
that.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_rebuild_random_bots import (
    CATALOG,
    CONF,
    CONF_TEXT,
    TOKEN,
    TORTOISE,
    World,
    _conf,
    _NoBots,
    _plan_and_restore,
    _Restores,
)
from yulon import bot_population, reset_defaults, tuning
from yulon.controller_wow_tortoise import poolreset
from yulon.ui import controller_view as controller_view_module
from yulon.ui.widgets.job import run_inline

KEY = poolreset.KEY
ARMED = f"{KEY} = once:{TOKEN}"
NOT_PUT_BACK = "was not put back"
"""A stable piece of `poolreset.REQUEST_NOT_PUT_BACK`, the line each route reports."""


def _card_view(
    tmp_path: Path, seam: object | None, entry: object = TORTOISE
) -> tuple[controller_view_module.ControllerView, _Restores]:
    """A view with the Playerbots card (the real bot-count route) AND the Maintenance restore."""
    from tests.test_controller_view import _Ps, _services

    made = _Restores(tmp_path / CONF)
    services = replace(
        _services(_Ps(), tmp_path, [], made.inner),  # type: ignore[arg-type]
        bots=_NoBots(),
        bot_pool_rebuild=seam,
        restore=made.do_restore,
        bot_population=bot_population.bot_count_route(entry, tmp_path),  # type: ignore[arg-type]
    )
    view = controller_view_module.ControllerView(
        entry, services, status_poll_ms=0, job_runner=run_inline  # type: ignore[arg-type]
    )
    return view, made


def _armed_then_restored(
    tmp_path: Path,
) -> tuple[Path, Path, controller_view_module.ControllerView]:
    """The ticket's path up to the Revert press. Returns the conf, the armed backup, the view.

    A pending request (left by a part-way reset, a stopped watch or a timeout) ->
    the bot count is changed, and that write backs the file up WITH `once:` ->
    a Maintenance restore sets the key to off.
    """
    path = _conf(tmp_path)
    poolreset.write_key(TORTOISE, tmp_path, TOKEN)
    bot_population.write(TORTOISE, tmp_path, 400)
    armed = tuning.backups_of(path)[-1]
    assert ARMED in armed.read_text() and "MaxRandomBots = 500" in armed.read_text()
    view, made = _card_view(tmp_path, World(tmp_path).rebuild())
    _plan_and_restore(view, tmp_path)
    assert made.keys_at_restore == ["off"], "T144's restore gate cleared it"
    assert poolreset.setting(tmp_path) == "off"
    return path, armed, view


def _with(text: bytes, value: str) -> bytes:
    """`text` with the one key line's value changed, every other byte kept."""
    return text.replace(ARMED.encode(), f"{KEY} = {value}".encode())


# ------------------------------------------------------------------ the three routes


def test_the_cards_revert_after_a_restore_does_not_arm_the_request_again(
    qapp: object, tmp_path: Path
) -> None:
    """The ticket's path, through the real Playerbots card. The bot count goes back (500);
    the request stays off, because the restored databases carry no record of it."""
    path, armed, view = _armed_then_restored(tmp_path)
    view.revert_tuning(*bot_population.CARD)
    assert poolreset.setting(tmp_path) == "off"
    assert path.read_bytes() == _with(armed.read_bytes(), "off"), "every other byte is the backup's"
    report = view.tuning_report.toPlainText()
    assert armed.name in report and NOT_PUT_BACK in report and f"once:{TOKEN}" in report


def test_the_raw_editors_revert_after_a_restore_does_not_arm_the_request_again(
    qapp: object, tmp_path: Path
) -> None:
    """The raw editor lists a file only when an installed module's manifest keys a setting in
    it, and no shipped manifest keys one in Tortoise's `aiplayerbot.conf` -- so it is listed
    here through the panel's own picker, for the route's sake: a later manifest row must not
    reopen the hole."""
    path, armed, view = _armed_then_restored(tmp_path)
    view.tuning_panel.set_files((CONF,))
    assert view.tuning_panel.current_file() == CONF
    view.revert_tuning_file()
    assert poolreset.setting(tmp_path) == "off"
    assert path.read_bytes() == _with(armed.read_bytes(), "off")
    report = view.tuning_report.toPlainText()
    assert armed.name in report and NOT_PUT_BACK in report
    assert f"{KEY} = off" in view.tuning_panel.editor.toPlainText(), "the editor shows the file"


def test_undo_of_a_reset_to_default_does_not_arm_the_request_again(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Reset to default's backup is a Tuning backup too (`<file>.<stamp>.reset-<press>.bak`),
    and Undo copies it back: the same rule, the other keys put back as the reset found them."""
    before = CONF_TEXT.replace("= off", f"= once:{TOKEN}").replace(
        "MaxRandomBots = 500", "MaxRandomBots = 700"
    )
    path = _conf(tmp_path, before)
    kept = reset_defaults.reset_backup(path, reset_defaults.new_press())
    path.write_bytes(CONF_TEXT.encode("utf-8"))  # what the reset wrote: the install's own, off
    view, _ = _card_view(tmp_path, World(tmp_path).rebuild())
    monkeypatch.setattr(view, "_confirm", lambda *_a: True)
    view._look_up_reset_undo()
    view.undo_last_reset()
    assert poolreset.setting(tmp_path) == "off"
    assert path.read_bytes() == _with(kept.read_bytes(), "off")
    assert b"MaxRandomBots = 700" in path.read_bytes(), "the rest is the reset's backup"
    assert NOT_PUT_BACK in view.tuning_report.toPlainText()


# ------------------------------------------------------------------ Revert is otherwise unchanged


def test_revert_without_a_request_puts_the_backup_back_byte_for_byte(
    qapp: object, tmp_path: Path
) -> None:
    path = _conf(tmp_path)
    bot_population.write(TORTOISE, tmp_path, 400)
    [backup] = tuning.backups_of(path)
    view, _ = _card_view(tmp_path, World(tmp_path).rebuild())
    view.revert_tuning(*bot_population.CARD)
    assert path.read_bytes() == backup.read_bytes() == CONF_TEXT.encode("utf-8")
    assert NOT_PUT_BACK not in view.tuning_report.toPlainText()


def test_revert_while_the_same_request_is_still_pending_keeps_it(
    qapp: object, tmp_path: Path
) -> None:
    """No restore in between: the file still asks for the same rebuild, so nothing is armed
    that is not armed already -- the backup goes back whole."""
    path = _conf(tmp_path)
    poolreset.write_key(TORTOISE, tmp_path, TOKEN)
    bot_population.write(TORTOISE, tmp_path, 400)
    armed = tuning.backups_of(path)[-1]
    view, _ = _card_view(tmp_path, World(tmp_path).rebuild())
    view.revert_tuning(*bot_population.CARD)
    assert path.read_bytes() == armed.read_bytes()
    assert poolreset.setting(tmp_path) == f"once:{TOKEN}"
    assert NOT_PUT_BACK not in view.tuning_report.toPlainText()


def test_revert_can_still_clear_a_request(qapp: object, tmp_path: Path) -> None:
    """A backup with the key off over a file that asks for a rebuild: put back whole."""
    path = _conf(tmp_path)
    bot_population.write(TORTOISE, tmp_path, 400)
    [backup] = tuning.backups_of(path)
    path.write_text(path.read_text().replace(f"{KEY} = off", ARMED))
    view, _ = _card_view(tmp_path, World(tmp_path).rebuild())
    view.revert_tuning(*bot_population.CARD)
    assert path.read_bytes() == backup.read_bytes()
    assert poolreset.setting(tmp_path) == "off"


@pytest.mark.parametrize(
    ("now", "backup", "after"),
    [
        ("off", "always", "off"),
        ("once:yulon-new", f"once:{TOKEN}", "once:yulon-new"),
        ("always", f"once:{TOKEN}", "always"),
        ("banana", f"once:{TOKEN}", "banana"),
    ],
)
def test_a_backup_never_brings_a_rebuild_the_file_does_not_ask_for_now(
    tmp_path: Path, now: str, backup: str, after: str
) -> None:
    """The rule, on the real seam: a restore may clear a request, never arm one. `always`
    rebuilds at every start, and a token that is not on disk now is a rebuild nobody asked
    for since whatever restore came in between."""
    path = _conf(tmp_path, CONF_TEXT.replace("= off", f"= {backup}"))
    made = tuning.backup(path)
    _conf(tmp_path, CONF_TEXT.replace("= off", f"= {now}"))
    note = World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert poolreset.setting(tmp_path) == after
    assert note is not None and NOT_PUT_BACK in note
    assert path.read_bytes() == CONF_TEXT.replace("= off", f"= {after}").encode("utf-8")


def test_another_file_is_put_back_as_it_was(tmp_path: Path) -> None:
    """Only `aiplayerbot.conf` is the bot config the module reads the key from
    (`PlayerbotAIConfig::Initialize`); any other file is copied back untouched."""
    other = tmp_path / "etc" / "modules" / "tortoise_bots.conf"
    other.parent.mkdir(parents=True)
    other.write_text(f"{ARMED}\n")
    made = tuning.backup(other)
    other.write_text(f"{KEY} = off\n")
    _conf(tmp_path)
    assert World(tmp_path).rebuild().put_back_file(made, other, tuning.restore) is None
    assert other.read_bytes() == made.read_bytes()


def test_another_games_revert_is_untouched(qapp: object, tmp_path: Path) -> None:
    """TBC's playerbots never read the key: no seam, and the backup goes back whole."""
    tbc = CATALOG.get("wow-tbc")
    path = _conf(tmp_path, CONF_TEXT.replace("= off", f"= once:{TOKEN}"))
    bot_population.write(tbc, tmp_path, 400)
    [backup] = tuning.backups_of(path)
    path.write_bytes(CONF_TEXT.encode("utf-8"))
    view, _ = _card_view(tmp_path, None, entry=tbc)
    view.revert_tuning(*bot_population.CARD)
    assert path.read_bytes() == backup.read_bytes()


def test_a_backup_that_is_not_text_and_asks_for_a_rebuild_is_not_put_back(
    qapp: object, tmp_path: Path
) -> None:
    """The key cannot be set in a file that will not decode, so the Revert refuses rather
    than copy the request back."""
    path = _conf(tmp_path)
    bot_population.write(TORTOISE, tmp_path, 400)
    [backup] = tuning.backups_of(path)
    backup.write_bytes(CONF_TEXT.replace("= off", f"= once:{TOKEN}").encode() + b"# \xff\n")
    current = path.read_bytes()
    view, _ = _card_view(tmp_path, World(tmp_path).rebuild())
    view.revert_tuning(*bot_population.CARD)
    assert path.read_bytes() == current
    assert "UTF-8" in view.tuning_report.toPlainText()


# ------------------------------------------------------------------ the module's own reading


@pytest.mark.parametrize(
    ("text", "value"),
    [
        (f"  {ARMED}\n", f"once:{TOKEN}"),
        (f"\t{ARMED}\r\n", f"once:{TOKEN}"),
        (f'{KEY} = "once:{TOKEN}"\n', f"once:{TOKEN}"),
        (f'{KEY}="once:{TOKEN}"  \n', f"once:{TOKEN}"),
        (f";{ARMED}\n", "off"),
        (f"  # {ARMED}\n", "off"),
        (f"{ARMED}\n{KEY} = off\n", "off"),
        (f"{KEY} = off\n    {ARMED}\n", f"once:{TOKEN}"),
        (f'{KEY} = "once:{TOKEN}\n', f'"once:{TOKEN}'),
    ],
    ids=[
        "indented",
        "tab-indented-crlf",
        "quoted",
        "quoted-no-spaces",
        "semicolon-comment",
        "indented-hash-comment",
        "a-later-off-wins",
        "a-later-indented-once-wins",
        "one-quote-is-kept",
    ],
)
def test_the_setting_is_read_the_way_the_module_reads_it(
    tmp_path: Path, text: str, value: str
) -> None:
    _conf(tmp_path, "[AiPlayerbotConf]\n" + text)
    assert poolreset.setting(tmp_path) == value


def test_the_restore_gate_sees_an_indented_request(tmp_path: Path) -> None:
    path = _conf(tmp_path, CONF_TEXT.replace(f"{KEY} = off", f"   {KEY} = once:{TOKEN}"))
    taken = poolreset.before_restore(TORTOISE, tmp_path)
    assert taken is not None and taken.previous == f"once:{TOKEN}"
    assert poolreset.setting(tmp_path) == "off"
    assert f"once:{TOKEN}" not in path.read_text()


def test_the_restore_gate_sees_a_quoted_request(tmp_path: Path) -> None:
    _conf(tmp_path, CONF_TEXT.replace(f"{KEY} = off", f'{KEY} = "once:{TOKEN}"'))
    taken = poolreset.before_restore(TORTOISE, tmp_path)
    assert taken is not None and taken.previous == f"once:{TOKEN}"
    assert poolreset.setting(tmp_path) == "off"


def test_the_writer_moves_every_copy_the_module_reads(tmp_path: Path) -> None:
    """A later, indented copy is the one the module obeys: taking the request back must move
    it too, not only the column-0 line `conf.patch` matches."""
    text = CONF_TEXT + f"  {KEY} = once:{TOKEN}\r\n"
    path = _conf(tmp_path, text)
    poolreset.take_back(TORTOISE, tmp_path)
    assert poolreset.setting(tmp_path) == "off"
    assert f"once:{TOKEN}" not in path.read_text()
    assert path.read_bytes() == (CONF_TEXT + f"{KEY} = off\r\n").encode("utf-8")


def test_a_write_the_module_would_read_differently_is_refused(tmp_path: Path) -> None:
    """`conf.patch` splits lines where Python does (a form feed too); ACE splits at `\\n`
    only. When the two disagree the writer refuses rather than write a value the module
    reads as something else."""
    text = CONF_TEXT.replace(f"{KEY} = off", f"{KEY} = once:{TOKEN}\x0ctail")
    path = _conf(tmp_path, text)
    with pytest.raises(poolreset.PoolResetError):
        poolreset.take_back(TORTOISE, tmp_path)
    assert path.read_bytes() == text.encode("utf-8")


def test_a_file_that_cannot_be_read_now_counts_as_off(tmp_path: Path) -> None:
    """Nothing says what the file asks for now: it has no key lines to keep, so the backup
    goes back without any, which the module reads as off."""
    path = _conf(tmp_path, CONF_TEXT.replace("= off", f"= once:{TOKEN}"))
    made = tuning.backup(path)
    path.write_bytes(b"# \xff not text\n")
    note = World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert note is not None and NOT_PUT_BACK in note
    assert path.read_bytes() == CONF_TEXT.replace(f"{KEY} = off\r\n", "").encode("utf-8")
    assert poolreset.setting(tmp_path) == "off"


# ------------------------------------------------------------------ round 2: sections (Codex)
#
# ACE files each `[section]`'s names separately, and the module's `GetValueHelper`
# returns the first section, in the order `enumerate_sections` walks its hash
# map, that has the key -- not a file order anyone can predict. So across
# sections Yu'lon fails safe: the file asks for a rebuild if ANY section's value
# asks for one, and disagreeing sections never read as off. Inside one section
# the last assignment still wins.

MAIN = "[AiPlayerbotConf]\n"
OTHER = "[Extra]\n"
ARMED_LINE = f"{ARMED}\n"
OFF_KEY_LINE = f"{KEY} = off\n"


def test_armed_in_the_main_section_and_off_in_a_later_one_is_seen_and_cleared(
    tmp_path: Path,
) -> None:
    path = _conf(tmp_path, MAIN + ARMED_LINE + OTHER + OFF_KEY_LINE)
    assert poolreset.setting(tmp_path) == f"once:{TOKEN}"
    taken = poolreset.before_restore(TORTOISE, tmp_path)
    assert taken is not None and taken.previous == f"once:{TOKEN}"
    assert path.read_text() == MAIN + OFF_KEY_LINE + OTHER + OFF_KEY_LINE


def test_off_first_and_armed_in_a_later_section_is_seen_and_cleared(tmp_path: Path) -> None:
    path = _conf(tmp_path, MAIN + OFF_KEY_LINE + OTHER + ARMED_LINE)
    assert poolreset.setting(tmp_path) == f"once:{TOKEN}"
    assert poolreset.before_restore(TORTOISE, tmp_path) is not None
    assert path.read_text() == MAIN + OFF_KEY_LINE + OTHER + OFF_KEY_LINE


def test_a_section_named_twice_is_one_section_and_its_last_assignment_wins(
    tmp_path: Path,
) -> None:
    """ACE compares section names case-blind (`ACE_Configuration_ExtId::operator==`), so a
    header met again reopens the same section: its later `off` replaces the `once:`."""
    _conf(tmp_path, MAIN + ARMED_LINE + "[aiplayerbotconf]\n" + OFF_KEY_LINE)
    assert poolreset.setting(tmp_path) == "off"


def test_lines_before_any_section_count_as_a_section_of_their_own(tmp_path: Path) -> None:
    _conf(tmp_path, ARMED_LINE + MAIN + OFF_KEY_LINE)
    assert poolreset.setting(tmp_path) == f"once:{TOKEN}"


@pytest.mark.parametrize("order", ["once-first", "always-first"])
def test_a_once_anywhere_is_what_the_file_says_so_a_restore_clears_every_section(
    tmp_path: Path, order: str
) -> None:
    """A `once:` beside an `always` is the value reported (the one the restore gate clears);
    the take-back then writes off in every section, the `always` included -- the module
    could have read either."""
    lines = [ARMED_LINE, f"{KEY} = always\n"]
    if order == "always-first":
        lines.reverse()
    path = _conf(tmp_path, MAIN + lines[0] + OTHER + lines[1])
    assert poolreset.setting(tmp_path) == f"once:{TOKEN}"
    assert poolreset.before_restore(TORTOISE, tmp_path) is not None
    assert path.read_text() == MAIN + OFF_KEY_LINE + OTHER + OFF_KEY_LINE


@pytest.mark.parametrize("beside", ["off", "banana"])
def test_always_in_one_section_is_warned_about_before_a_restore(
    tmp_path: Path, beside: str
) -> None:
    _conf(tmp_path, MAIN + f"{KEY} = {beside}\n" + OTHER + f"{KEY} = always\n")
    assert poolreset.restore_warning(tmp_path) == poolreset.ALWAYS_BEFORE_RESTORE
    _conf(tmp_path, MAIN + f"{KEY} = always\n" + OTHER + f"{KEY} = {beside}\n")
    assert poolreset.restore_warning(tmp_path) == poolreset.ALWAYS_BEFORE_RESTORE


@pytest.mark.parametrize("order", ["off-first", "off-last"])
def test_disagreeing_sections_never_read_as_off(tmp_path: Path, order: str) -> None:
    """Nothing asks for a rebuild here, but the sections disagree: the other value is shown."""
    lines = [OFF_KEY_LINE, f"{KEY} = banana\n"]
    if order == "off-last":
        lines.reverse()
    _conf(tmp_path, MAIN + lines[0] + OTHER + lines[1])
    assert poolreset.setting(tmp_path) == "banana"


def test_a_write_a_second_section_would_read_differently_is_refused(tmp_path: Path) -> None:
    """Every section is read back, not only the value `value_in` reports: here the first
    section reads the new token and the second reads it with a form feed and more."""
    text = MAIN + OFF_KEY_LINE + OTHER + f"{KEY} = off\x0ctail\n"
    path = _conf(tmp_path, text)
    with pytest.raises(poolreset.PoolResetError):
        poolreset.write_key(TORTOISE, tmp_path, TOKEN)
    assert path.read_bytes() == text.encode("utf-8")


def test_a_backup_armed_in_any_section_goes_back_with_the_files_own_key_lines(
    tmp_path: Path,
) -> None:
    """Round 4: the key lines are the current file's, section by section; the backup's
    `[Extra]` section, which the file lacks, gets none."""
    path = _conf(tmp_path, MAIN + ARMED_LINE + "X = 1\n" + OTHER + OFF_KEY_LINE)
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 2\n")
    note = World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert note is not None and NOT_PUT_BACK in note
    assert path.read_text() == MAIN + OFF_KEY_LINE + "X = 1\n" + OTHER


def test_a_backup_whose_other_section_would_add_always_is_not_put_back_whole(
    tmp_path: Path,
) -> None:
    """The backup's first section matches the file's, but its second asks for a rebuild the
    file does not: the file's key lines stand, and the second section gets none."""
    path = _conf(tmp_path, MAIN + ARMED_LINE + OTHER + f"{KEY} = always\n")
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + ARMED_LINE)
    note = World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert note is not None
    assert path.read_text() == MAIN + ARMED_LINE + OTHER


# ------------------------------------------------------------------ round 3: the module's own parse
#
# `ParsePoolResetSetting` (TortoiseBots 632e1b63, `runtime/PoolResetPolicy.h:58-108`)
# trims " \t\r\n" off the value ACE hands it (after ACE took the quotes off),
# lower-cases it to compare with `off`, `always` and the `once:` prefix, trims
# " \t" off the token, keeps the token's case, and calls a token that fails
# `IsValidPoolResetToken` (or is empty) Invalid: no reset.

PADDED = f'{KEY} = " once:{TOKEN} "\n'
PADDED_ALWAYS = f'{KEY} = " always "\n'


@pytest.mark.parametrize(
    "line",
    [PADDED, f"{KEY} = ONCE:{TOKEN}\n", f"{KEY} = once: {TOKEN}\n"],
    ids=["padded-quoted", "upper-case-prefix", "space-after-colon"],
)
@pytest.mark.parametrize("where", ["one-section", "second-section"])
def test_the_restore_gate_clears_a_request_however_the_module_would_spell_it(
    tmp_path: Path, line: str, where: str
) -> None:
    text = MAIN + line if where == "one-section" else MAIN + OFF_KEY_LINE + OTHER + line
    path = _conf(tmp_path, text)
    taken = poolreset.before_restore(TORTOISE, tmp_path)
    assert taken is not None
    assert TOKEN in taken.previous
    assert poolreset.setting(tmp_path) == "off"
    assert TOKEN not in path.read_text()


@pytest.mark.parametrize("line", [PADDED_ALWAYS, f"{KEY} = ALWAYS\n"], ids=["padded", "upper"])
@pytest.mark.parametrize("where", ["one-section", "second-section"])
def test_always_is_warned_about_however_the_module_would_spell_it(
    tmp_path: Path, line: str, where: str
) -> None:
    text = MAIN + line if where == "one-section" else MAIN + OFF_KEY_LINE + OTHER + line
    _conf(tmp_path, text)
    assert poolreset.restore_warning(tmp_path) == poolreset.ALWAYS_BEFORE_RESTORE


@pytest.mark.parametrize("line", [PADDED, PADDED_ALWAYS], ids=["once", "always"])
@pytest.mark.parametrize("where", ["one-section", "second-section"])
def test_a_padded_request_in_a_backup_is_not_put_back_over_off(
    tmp_path: Path, line: str, where: str
) -> None:
    text = MAIN + line if where == "one-section" else MAIN + OFF_KEY_LINE + OTHER + line
    path = _conf(tmp_path, text)
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE)
    note = World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert note is not None and NOT_PUT_BACK in note
    assert set(poolreset.values_in(path.read_text())) == {"off"}


def test_the_same_request_spelt_otherwise_keeps_the_files_spelling(tmp_path: Path) -> None:
    """Round 4: a backup that asks for a rebuild never supplies the key, however it spells
    it; the file's own line stays, and the rest is the backup's."""
    path = _conf(tmp_path, MAIN + f"{KEY} = ONCE:{TOKEN}\nX = 1\n")
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + ARMED_LINE + "X = 2\n")
    assert World(tmp_path).rebuild().put_back_file(made, path, tuning.restore) is not None
    assert path.read_text() == MAIN + ARMED_LINE + "X = 1\n"


def test_a_token_differing_only_in_case_is_another_request(tmp_path: Path) -> None:
    """The token keeps its case (`value.substr(5)`), and generations compare case-sensitively."""
    path = _conf(tmp_path, MAIN + f"{KEY} = once:{TOKEN.lower()}\n")
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + ARMED_LINE)
    assert World(tmp_path).rebuild().put_back_file(made, path, tuning.restore) is not None
    assert poolreset.setting(tmp_path) == f"once:{TOKEN}"


@pytest.mark.parametrize(
    "value",
    ["once:", "once:a b", "once:" + "x" * 129, f'"\x0bonce:{TOKEN}"'],
    ids=["empty-token", "space-in-token", "129-characters", "vertical-tab-inside-quotes"],
)
def test_a_once_the_module_calls_invalid_is_no_request(tmp_path: Path, value: str) -> None:
    """Invalid schedules nothing (`ShouldResetForGeneration` is false for it), so the gate
    leaves it and a backup holding it goes back whole. The last one: ACE takes the quotes
    off, and the module trims " \t\r\n" only, so the vertical tab stays and the value is
    not `once:`."""
    path = _conf(tmp_path, MAIN + f"{KEY} = {value}\n")
    assert poolreset.before_restore(TORTOISE, tmp_path) is None
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE)
    assert World(tmp_path).rebuild().put_back_file(made, path, tuning.restore) is None
    assert path.read_bytes() == made.read_bytes()


def test_a_padded_request_is_put_back_after_a_restore_that_loaded_nothing(
    tmp_path: Path,
) -> None:
    """The raw value goes back; the read-back compares what the module would read."""
    _conf(tmp_path, MAIN + PADDED)
    taken = poolreset.before_restore(TORTOISE, tmp_path)
    assert taken is not None
    said = poolreset.after_a_failed_restore(TORTOISE, tmp_path, taken, loaded=False)
    assert "put back as it was" in said
    assert poolreset.setting(tmp_path) == f"once:{TOKEN}", "ACE trims the unquoted padding"


def test_a_padded_once_beside_always_is_the_one_the_restore_gate_clears(tmp_path: Path) -> None:
    """Sections are ranked by what the module makes of them: the padded `once:` is a once."""
    path = _conf(tmp_path, MAIN + f"{KEY} = always\n" + OTHER + PADDED)
    assert poolreset.before_restore(TORTOISE, tmp_path) is not None
    assert path.read_text() == MAIN + OFF_KEY_LINE + OTHER + OFF_KEY_LINE


def test_an_invalid_once_beside_always_does_not_hide_the_always(tmp_path: Path) -> None:
    _conf(tmp_path, MAIN + f"{KEY} = once:a b\n" + OTHER + f"{KEY} = always\n")
    assert poolreset.restore_warning(tmp_path) == poolreset.ALWAYS_BEFORE_RESTORE


# ------------------------------------------------------ round 4: the file's own key lines
#
# Codex on round 3: comparing the backup with ONE value standing for the file
# is unsafe when the file's sections disagree -- the module may be reading any
# of them. So a backup that asks for a rebuild anywhere never supplies the key:
# the file's key lines stand, section by section, as they are.

ONCE_A = f"{KEY} = once:yulon-A\n"
ONCE_B = f"{KEY} = once:yulon-B\n"


def _revert(tmp_path: Path, backup_text: str, current_text: str) -> tuple[Path, Path, str | None]:
    path = _conf(tmp_path, backup_text)
    made = tuning.backup(path)
    _conf(tmp_path, current_text)
    return path, made, World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)


def test_two_current_requests_both_stand_when_the_backup_holds_one_of_them(
    tmp_path: Path,
) -> None:
    """Codex's case: the file asks `once:A` in one section and `once:B` in another; the backup
    holds only `once:A`. Put back whole, B would be gone -- and if B is the one the module
    reads and has recorded, A is new and the next start rebuilds."""
    path, _, note = _revert(tmp_path, MAIN + ONCE_A + "X = 1\n", MAIN + ONCE_A + OTHER + ONCE_B)
    assert note is not None and "once:yulon-A" in note
    assert path.read_text() == MAIN + ONCE_A + "X = 1\n" + OTHER + ONCE_B
    assert poolreset.values_in(path.read_text()) == ("once:yulon-A", "once:yulon-B")


def test_a_request_in_the_backups_second_section_is_dropped_over_a_file_that_is_off(
    tmp_path: Path,
) -> None:
    path, _, note = _revert(
        tmp_path, MAIN + OFF_KEY_LINE + "X = 1\n" + OTHER + ARMED_LINE, MAIN + OFF_KEY_LINE
    )
    assert note is not None
    assert path.read_text() == MAIN + OFF_KEY_LINE + "X = 1\n" + OTHER


def test_always_in_the_file_stands_over_a_backups_once(tmp_path: Path) -> None:
    path, _, note = _revert(tmp_path, MAIN + ARMED_LINE + "X = 1\n", MAIN + f"{KEY} = always\n")
    assert note is not None
    assert path.read_text() == MAIN + f"{KEY} = always\n" + "X = 1\n"


def test_a_section_the_backup_lacks_keeps_the_files_key_line(tmp_path: Path) -> None:
    """The file's key lines before any header go first; a section only the file has is
    carried over with its header."""
    path, _, _ = _revert(tmp_path, MAIN + ARMED_LINE, ONCE_B + MAIN + OFF_KEY_LINE + OTHER + ONCE_A)
    assert path.read_text() == ONCE_B + MAIN + OFF_KEY_LINE + OTHER + ONCE_A


def test_a_section_the_backup_has_without_the_key_gets_the_files_line_under_its_header(
    tmp_path: Path,
) -> None:
    path, _, _ = _revert(
        tmp_path, MAIN + ARMED_LINE + OTHER + "Y = 1\n", MAIN + OFF_KEY_LINE + OTHER + ONCE_B
    )
    assert path.read_text() == MAIN + OFF_KEY_LINE + OTHER + ONCE_B + "Y = 1\n"


def test_matching_key_lines_go_back_byte_for_byte(tmp_path: Path) -> None:
    """The backup's key lines are the file's own, two disagreeing sections included: nothing
    changes about the request, so the backup goes back exactly, with no note. Since round 5
    it is written from the bytes inspected, not by the route's copy."""
    path = _conf(tmp_path, MAIN + ONCE_A + "X = 1\n" + OTHER + ONCE_B)
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + ONCE_A + "X = 2\n" + OTHER + ONCE_B)
    assert World(tmp_path).rebuild().put_back_file(made, path, tuning.restore) is None
    assert path.read_bytes() == made.read_bytes()


def test_the_files_key_line_takes_the_place_of_the_backups(tmp_path: Path) -> None:
    path, _, _ = _revert(tmp_path, MAIN + "X = 1\n" + ARMED_LINE + "Y = 1\n", MAIN + ONCE_B)
    assert path.read_text() == MAIN + "X = 1\n" + ONCE_B + "Y = 1\n"


# ------------------------------------------------------ round 5: what was read is written
#
# Codex on round 4: the backup was inspected, then copied again by path; and the
# file's key lines were read, then the file replaced without a look. Now the
# bytes inspected are the bytes written, and the file is read again just before
# the replace: changed since, nothing is written.

CHANGED = "changed on disk while the copy was being put back"


def _after_reading(
    monkeypatch: pytest.MonkeyPatch, watched: Path, then: Callable[[], None]
) -> list[bool]:
    """Run `then` once, right after the first read of `watched` -- by `read_bytes` or `open`.

    `then` writes by `os.replace`, so a handle already open keeps the old bytes: the
    reader has what it read, and the disk has moved on, whichever way the code reads.
    """
    fired: list[bool] = []
    real_read_bytes, real_open = Path.read_bytes, Path.open

    def fire(path: Path) -> None:
        if path == watched and not fired:
            fired.append(True)
            then()

    def read_bytes(self: Path) -> bytes:
        data = real_read_bytes(self)
        fire(self)
        return data

    def open_(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        handle = real_open(self, mode, *args, **kwargs)  # type: ignore[call-overload]
        if "r" in mode and "+" not in mode:
            fire(self)
        return handle

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "open", open_)
    return fired


def _swap(path: Path, text: str) -> Callable[[], None]:
    def write() -> None:
        side = path.with_name(path.name + ".swap")
        side.write_bytes(text.encode("utf-8"))
        os.replace(side, path)

    return write


def test_a_file_cleared_while_its_key_lines_were_read_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file asks `once:B`; the backup asks `once:A`. Between reading the file and writing
    the result, another path clears the request: writing the key lines read before would
    arm B again."""
    path = _conf(tmp_path, MAIN + ONCE_A + "X = 1\n")
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + ONCE_B + "X = 2\n")
    cleared = MAIN + OFF_KEY_LINE + "X = 2\n"
    fired = _after_reading(monkeypatch, path, _swap(path, cleared))
    with pytest.raises(OSError, match=CHANGED):
        World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert fired
    assert path.read_text() == cleared, "the clear stands"
    assert not list(tmp_path.glob("etc/*tmp*")), "no temporary file left behind"


def test_a_file_edited_while_a_plain_backup_goes_back_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole-backup branch too: a backup with no request, a file edited meanwhile."""
    path = _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 1\n")
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 2\n")
    edited = MAIN + ARMED_LINE + "X = 3\n"
    _after_reading(monkeypatch, path, _swap(path, edited))
    with pytest.raises(OSError, match=CHANGED):
        World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert path.read_text() == edited


def test_a_backup_changed_after_it_was_inspected_is_not_what_goes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backup read as `off` is what is written, even when the file at its path has come
    to say `once:` since."""
    inspected = MAIN + OFF_KEY_LINE + "X = 1\n"
    path = _conf(tmp_path, inspected)
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 2\n")
    _after_reading(monkeypatch, made, _swap(made, MAIN + ARMED_LINE + "X = 1\n"))
    assert World(tmp_path).rebuild().put_back_file(made, path, tuning.restore) is None
    assert path.read_text() == inspected


def test_a_rewritten_backup_is_the_inspected_one_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspected = MAIN + ARMED_LINE + "X = 1\n"
    path = _conf(tmp_path, inspected)
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 2\n")
    _after_reading(monkeypatch, made, _swap(made, MAIN + ARMED_LINE + "X = 9\n"))
    assert World(tmp_path).rebuild().put_back_file(made, path, tuning.restore) is not None
    assert path.read_text() == MAIN + OFF_KEY_LINE + "X = 1\n"


def test_a_whole_backup_keeps_its_mode_and_times_as_the_copy_did(tmp_path: Path) -> None:
    path = _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 1\n")
    os.chmod(path, 0o640)
    os.utime(path, (1_000_000_000, 1_000_000_000))
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 2\n")
    os.chmod(path, 0o600)
    assert World(tmp_path).rebuild().put_back_file(made, path, tuning.restore) is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert path.stat().st_mtime == 1_000_000_000
    assert not list(tmp_path.glob("etc/*tmp*")), "no temporary file left behind"


def test_a_rewritten_backup_keeps_the_files_own_mode(tmp_path: Path) -> None:
    path = _conf(tmp_path, MAIN + ARMED_LINE + "X = 1\n")
    os.chmod(path, 0o600)
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 2\n")
    os.chmod(path, 0o644)
    assert World(tmp_path).rebuild().put_back_file(made, path, tuning.restore) is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
