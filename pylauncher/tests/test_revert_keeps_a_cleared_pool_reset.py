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
    """Nothing says what the file asks for now, so the backup's request is not brought back."""
    path = _conf(tmp_path, CONF_TEXT.replace("= off", f"= once:{TOKEN}"))
    made = tuning.backup(path)
    path.write_bytes(b"# \xff not text\n")
    note = World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert note is not None and NOT_PUT_BACK in note
    assert path.read_bytes() == CONF_TEXT.encode("utf-8")


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


def test_a_backup_armed_in_any_section_is_put_back_with_every_section_kept_off(
    tmp_path: Path,
) -> None:
    path = _conf(tmp_path, MAIN + ARMED_LINE + "X = 1\n" + OTHER + OFF_KEY_LINE)
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + OFF_KEY_LINE + "X = 2\n")
    note = World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert note is not None and NOT_PUT_BACK in note
    assert path.read_text() == MAIN + OFF_KEY_LINE + "X = 1\n" + OTHER + OFF_KEY_LINE


def test_a_backup_whose_other_section_would_add_always_is_not_put_back_whole(
    tmp_path: Path,
) -> None:
    """The backup's reported value equals the file's, but its second section asks for a
    rebuild the file does not: that section is kept at the file's value too."""
    path = _conf(tmp_path, MAIN + ARMED_LINE + OTHER + f"{KEY} = always\n")
    made = tuning.backup(path)
    _conf(tmp_path, MAIN + ARMED_LINE)
    note = World(tmp_path).rebuild().put_back_file(made, path, tuning.restore)
    assert note is not None
    assert path.read_text() == MAIN + ARMED_LINE + OTHER + ARMED_LINE
