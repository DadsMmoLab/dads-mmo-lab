"""T99: how many random bots a server runs, read and changed in the install's own place.

A player on #yulon asked "is there a way to lower the bot count?". Every install
pins it to 500 (Min = Max = 500), and each game keeps it where its install wrote
it: the three CMaNGOS games in `etc/aiplayerbot.conf`, WotLK in the compose
override's environment (which wins over `playerbots.conf`). Every test here that
could compare a write against a copy of its own logic compares it against the
INSTALL's own output instead.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from yulon import bot_population, channel_setup, resources, tuning
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
VANILLA = CATALOG.get("wow-vanilla")
TORTOISE = CATALOG.get("wow-tortoise")
OVERRIDE = composegen.OVERRIDE_FILE
CONF = "etc/aiplayerbot.conf"

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def _linux() -> str:
    return "linux"


def _cmangos_conf(server: Path, *, accounts: str | None = "100", crlf: bool = False) -> bytes:
    """An `aiplayerbot.conf` shaped like the one the install leaves (upstream text, 500/500)."""
    lines = [
        "# Random bot count",
        "AiPlayerbot.MinRandomBots = 500",
        "AiPlayerbot.MaxRandomBots = 500",
        "AiPlayerbot.RandomBotMinLevel = 1",
        "",
        "# Accounts to create for random bots",
    ]
    if accounts is not None:
        lines.append(f"AiPlayerbot.RandomBotAccountCount = {accounts}")
    lines.append("#AiPlayerbot.MaxRandomBotsPriceChangeInterval = 172800")
    ending = "\r\n" if crlf else "\n"
    data = (ending.join(lines) + ending).encode("utf-8")
    path = server / CONF
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return data


def _wotlk_stack(server: Path, *, channel: bool = False) -> bytes:
    """The override the install's OWN compose stage writes (and the channel press, if asked)."""
    engine = AzerothCoreInstaller(
        WOTLK,
        seams=native.Seams(
            platform_id=_linux, selinux_enforcing=lambda: False, fs_type=lambda path: "ext4"
        ),
    )
    context = native.StageContext(
        server_dir=server,
        client_dir=None,
        state=native.InstallState(
            game_id=WOTLK.id,
            install_id=composegen.install_id(server, platform_id=_linux),
            family="azerothcore",
        ),
        cancel=None,
        secrets=native.Secrets(db_password="password"),
    )
    list(engine.stage_generate_compose(context))
    if channel:
        channel_setup.enable(
            WOTLK, server, templates_root=resources.installers_dir(), world_running=False
        )
    return (server / OVERRIDE).read_bytes()


def _baks(server: Path) -> list[Path]:
    return sorted(server.rglob("*.bak"))


# -- where each game keeps it ------------------------------------------------------


def test_each_game_keeps_its_bot_count_where_its_install_writes_it() -> None:
    """Read off the install table, not a game id: the catalog is the one place it is written."""
    for entry in (TBC, VANILLA, TORTOISE):
        assert bot_population.where(entry) == (CONF, "conf"), entry.id
        table = entry.install.native.cmangos.conf.files["aiplayerbot.conf"].keys
        assert table[bot_population.MIN_KEY] == table[bot_population.MAX_KEY] == "500"
    assert bot_population.where(WOTLK) == (OVERRIDE, "env")
    env = composegen.world_env(WOTLK)
    assert env[bot_population.MIN_ENV] == env[bot_population.MAX_ENV] == "500"


def test_the_env_names_are_the_ones_azerothcore_reads_for_the_two_conf_keys() -> None:
    assert bot_population.MIN_ENV == composegen.env_name_for(bot_population.MIN_KEY)
    assert bot_population.MAX_ENV == composegen.env_name_for(bot_population.MAX_KEY)
    assert bot_population.MAX_ENV == "AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"


# -- the value round-trips, per game -------------------------------------------------


@POSIX
@pytest.mark.parametrize("entry", [TBC, VANILLA, TORTOISE], ids=lambda e: e.id)
def test_a_cmangos_count_round_trips_and_only_its_two_lines_move(
    tmp_path: Path, entry: object
) -> None:
    before = _cmangos_conf(tmp_path)
    reading = bot_population.read(entry, tmp_path)  # type: ignore[arg-type]
    assert (reading.min, reading.max, reading.problem) == (500, 500, None)

    written = bot_population.write(entry, tmp_path, 50)  # type: ignore[arg-type]

    after = (tmp_path / CONF).read_bytes()
    assert after == before.replace(b"Bots = 500", b"Bots = 50")
    assert b"MaxRandomBotsPriceChangeInterval = 172800" in after, "a longer key is not touched"
    again = bot_population.read(entry, tmp_path)  # type: ignore[arg-type]
    assert (again.min, again.max) == (50, 50)
    assert written.rule == "restart", "./etc is bound into the containers"
    assert written.backup is not None and written.backup.read_bytes() == before
    assert stat.S_IMODE((tmp_path / CONF).stat().st_mode) == 0o600, "the conf keeps its mode"
    assert stat.S_IMODE(written.backup.stat().st_mode) == 0o600, "and so does its backup"


def test_a_crlf_conf_stays_crlf(tmp_path: Path) -> None:
    before = _cmangos_conf(tmp_path, crlf=True)
    bot_population.write(TBC, tmp_path, 120)
    assert (tmp_path / CONF).read_bytes() == before.replace(b"Bots = 500", b"Bots = 120")


def test_the_wotlk_count_round_trips_in_the_installs_own_override(tmp_path: Path) -> None:
    installed = _wotlk_stack(tmp_path).decode("utf-8")
    reading = bot_population.read(WOTLK, tmp_path)
    assert (reading.min, reading.max, reading.problem) == (500, 500, None)

    written = bot_population.write(WOTLK, tmp_path, 50)

    now = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert now == installed.replace(
        'AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "500"', 'AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "50"'
    ).replace('AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "500"', 'AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "50"')
    again = bot_population.read(WOTLK, tmp_path)
    assert (again.min, again.max) == (50, 50)
    assert written.rule == "recreate", "container environment is read when it is created"
    assert written.backup is not None and written.backup.read_text(encoding="utf-8") == installed


def test_a_live_command_channel_keeps_its_env_when_the_count_changes(tmp_path: Path) -> None:
    pressed = _wotlk_stack(tmp_path, channel=True).decode("utf-8")
    assert "AC_SOAP_ENABLED" in pressed, "control: the press wrote the channel env"

    bot_population.write(WOTLK, tmp_path, 75)

    now = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert "AC_SOAP_ENABLED" in now
    assert now == pressed.replace('RANDOM_BOTS: "500"', 'RANDOM_BOTS: "75"')


def _hand_tuned(installed: str) -> str:
    """The install's override as a player leaves it: `:z` binds, a hand-added key, a comment."""
    tuned = installed.replace("/azerothcore/modules\n", "/azerothcore/modules:z\n")
    assert tuned != installed, "control: the volume line is where this test expects it"
    return tuned.replace(
        "    environment:\n",
        "    environment:\n      # my own note, kept\n      TZ: 'Europe/Oslo'   # hand-added\n",
    )


@POSIX
def test_only_the_two_values_move_in_a_hand_tuned_override(tmp_path: Path) -> None:
    """Codex high: the box re-rendered the whole override, dropping hand lines and `:z`.

    Mutation: render the file again (f67483d0) and the TZ line, the note and
    `:z` go, and the mode the render's writer picks replaces the file's own.
    """
    installed = _wotlk_stack(tmp_path).decode("utf-8")
    before = _hand_tuned(installed)
    path = tmp_path / OVERRIDE
    path.write_text(before, encoding="utf-8", newline="")
    os.chmod(path, 0o640)

    written = bot_population.write(WOTLK, tmp_path, 60)

    assert path.read_text(encoding="utf-8") == before.replace(
        'RANDOM_BOTS: "500"', 'RANDOM_BOTS: "60"'
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert written.backup is not None and written.backup.read_text(encoding="utf-8") == before


def test_a_crlf_override_stays_crlf_and_a_bare_value_stays_bare(tmp_path: Path) -> None:
    installed = _wotlk_stack(tmp_path).decode("utf-8")
    before = installed.replace('MAX_RANDOM_BOTS: "500"', "MAX_RANDOM_BOTS: 500").replace(
        "\n", "\r\n"
    )
    (tmp_path / OVERRIDE).write_bytes(before.encode("utf-8"))
    bot_population.write(WOTLK, tmp_path, 70)
    assert (tmp_path / OVERRIDE).read_bytes() == before.replace(
        'MIN_RANDOM_BOTS: "500"', 'MIN_RANDOM_BOTS: "70"'
    ).replace("MAX_RANDOM_BOTS: 500", "MAX_RANDOM_BOTS: 70").encode("utf-8")


@pytest.mark.parametrize("case", ["missing", "twice"])
def test_a_key_missing_or_twice_in_the_world_env_is_refused(tmp_path: Path, case: str) -> None:
    """Which line the world reads is not a guess this box makes; Reset to default rebuilds."""
    installed = _wotlk_stack(tmp_path).decode("utf-8")
    line = '      AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "500"\n'
    assert line in installed, "control"
    before = installed.replace(line, "" if case == "missing" else line + line)
    (tmp_path / OVERRIDE).write_text(before, encoding="utf-8", newline="")

    reading = bot_population.read(WOTLK, tmp_path)
    assert reading.problem is not None and "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS" in reading.problem
    with pytest.raises(bot_population.BotCountError):
        bot_population.write(WOTLK, tmp_path, 60)
    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8") == before
    assert _baks(tmp_path) == []


def test_a_commented_copy_of_a_key_is_not_a_second_one(tmp_path: Path) -> None:
    installed = _wotlk_stack(tmp_path).decode("utf-8")
    before = installed.replace(
        "    environment:\n", '    environment:\n      # AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "9"\n'
    )
    (tmp_path / OVERRIDE).write_text(before, encoding="utf-8", newline="")
    bot_population.write(WOTLK, tmp_path, 80)
    now = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert '# AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "9"' in now
    assert 'AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "80"' in now


# -- nothing written when there is nothing to write ------------------------------------


def test_the_same_number_again_writes_nothing_and_takes_no_backup(tmp_path: Path) -> None:
    before = _cmangos_conf(tmp_path)
    written = bot_population.write(TBC, tmp_path, 500)
    assert written.backup is None and written.changed is False
    assert (tmp_path / CONF).read_bytes() == before
    assert _baks(tmp_path) == []


def test_min_and_max_that_differ_are_both_set_to_the_one_number(tmp_path: Path) -> None:
    """The box sets Min = Max = N, matching the install's 500 (a band is not offered)."""
    data = _cmangos_conf(tmp_path).replace(b"MinRandomBots = 500", b"MinRandomBots = 100")
    (tmp_path / CONF).write_bytes(data)
    reading = bot_population.read(TBC, tmp_path)
    assert (reading.min, reading.max) == (100, 500)
    written = bot_population.write(TBC, tmp_path, 500)
    assert written.changed is True
    assert bot_population.read(TBC, tmp_path).min == 500


# -- the range: read off the conf, never invented -----------------------------------------


def test_the_cmangos_ceiling_is_the_bot_character_pool_the_conf_allows(tmp_path: Path) -> None:
    """`RandomBotAccountCount` accounts x 9 characters (RandomPlayerbotFactory.cpp:755-760)."""
    _cmangos_conf(tmp_path, accounts="100")
    reading = bot_population.read(TBC, tmp_path)
    assert reading.ceiling == 900
    assert "RandomBotAccountCount" in reading.ceiling_why and "100" in reading.ceiling_why


@pytest.mark.parametrize("n", [-1, 901])
def test_a_number_outside_the_range_is_refused_before_anything_is_written(
    tmp_path: Path, n: int
) -> None:
    before = _cmangos_conf(tmp_path, accounts="100")
    with pytest.raises(bot_population.BotCountError):
        bot_population.write(TBC, tmp_path, n)
    assert (tmp_path / CONF).read_bytes() == before and _baks(tmp_path) == []


def test_zero_is_allowed_because_the_core_reads_it_as_no_random_bots(tmp_path: Path) -> None:
    _cmangos_conf(tmp_path)
    assert bot_population.write(TBC, tmp_path, 0).changed is True
    assert bot_population.read(TBC, tmp_path).max == 0


@pytest.mark.parametrize("accounts", ["0", "lots", "-3"])
def test_an_account_count_of_zero_or_nonsense_means_no_ceiling_is_known(
    tmp_path: Path, accounts: str
) -> None:
    """Codex medium: 0 gave the box a 0..0 range; nonsense is not a limit either."""
    _cmangos_conf(tmp_path, accounts=accounts)
    reading = bot_population.read(TBC, tmp_path)
    assert reading.ceiling == bot_population.NO_CEILING
    assert "RandomBotAccountCount" in reading.ceiling_why


def test_a_huge_account_count_is_clamped_to_the_whole_number_the_core_reads(
    tmp_path: Path,
) -> None:
    _cmangos_conf(tmp_path, accounts=str(10**12))
    assert bot_population.read(TBC, tmp_path).ceiling == bot_population.NO_CEILING


def test_a_conf_with_no_account_count_states_no_ceiling(tmp_path: Path) -> None:
    """Tortoise's module has no such key (T30 Half 1), and none is invented for it."""
    _cmangos_conf(tmp_path, accounts=None)
    reading = bot_population.read(TORTOISE, tmp_path)
    assert reading.ceiling == bot_population.NO_CEILING
    assert "no upper limit" in reading.ceiling_why


def test_wotlk_states_no_ceiling_either(tmp_path: Path) -> None:
    _wotlk_stack(tmp_path)
    assert bot_population.read(WOTLK, tmp_path).ceiling == bot_population.NO_CEILING


# -- refusals ------------------------------------------------------------------------------


def test_a_missing_conf_is_a_problem_to_read_and_a_refusal_to_write(tmp_path: Path) -> None:
    reading = bot_population.read(TBC, tmp_path)
    assert reading.problem is not None and "aiplayerbot.conf" in reading.problem
    assert reading.max is None
    with pytest.raises(bot_population.BotCountError):
        bot_population.write(TBC, tmp_path, 50)
    assert not (tmp_path / CONF).exists(), "the box does not create an install's files"


def test_another_tools_compose_stack_is_left_alone(tmp_path: Path) -> None:
    """A WotLK server adopted from the DML launcher: its compose files are not Yu'lon's."""
    installed = _wotlk_stack(tmp_path)
    (tmp_path / composegen.BASE_FILE).write_text("services: {}\n", encoding="utf-8")
    reading = bot_population.read(WOTLK, tmp_path)
    assert reading.problem is not None
    with pytest.raises(bot_population.BotCountError):
        bot_population.write(WOTLK, tmp_path, 50)
    assert (tmp_path / OVERRIDE).read_bytes() == installed


def test_a_conf_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    before = _cmangos_conf(tmp_path) + b"# caf\xe9\n"
    (tmp_path / CONF).write_bytes(before)
    assert bot_population.read(TBC, tmp_path).problem is not None
    with pytest.raises(bot_population.BotCountError):
        bot_population.write(TBC, tmp_path, 50)
    assert (tmp_path / CONF).read_bytes() == before


# -- the Tuning tab's rows ------------------------------------------------------------------


@pytest.mark.parametrize("entry", [TBC, VANILLA, TORTOISE], ids=lambda e: e.id)
def test_the_tuning_rows_are_the_keys_the_install_writes_into_aiplayerbot_conf(
    tmp_path: Path, entry: object
) -> None:
    _cmangos_conf(tmp_path)
    rows = bot_population.read(entry, tmp_path).rows  # type: ignore[arg-type]
    table = entry.install.native.cmangos.conf.files["aiplayerbot.conf"].keys  # type: ignore[attr-defined]
    assert [row.key for row in rows] == list(table)
    assert {row.file for row in rows} == {CONF}
    assert all(row.editable and row.backend == "conf" for row in rows)
    by_key = {row.key: row for row in rows}
    assert by_key[bot_population.MAX_KEY].current == "500"
    assert by_key[bot_population.MAX_KEY].type == "int"
    assert by_key[bot_population.MAX_KEY].min == 0
    assert by_key[bot_population.MAX_KEY].max is None, "no bound is invented"
    assert {(row.family, row.module_id) for row in rows} == {bot_population.CARD}


def test_wotlk_has_no_tuning_rows(tmp_path: Path) -> None:
    """Its count is container environment, not a conf key; the tab's raw editor warns about it."""
    _wotlk_stack(tmp_path)
    assert bot_population.read(WOTLK, tmp_path).rows == ()


def test_the_rows_spec_type_checks_a_count(tmp_path: Path) -> None:
    spec = bot_population.conf_keys(TBC)
    with pytest.raises(tuning.TuningError):
        tuning.check(spec[bot_population.MAX_KEY], "-5")
    tuning.check(spec[bot_population.MAX_KEY], "50")


# -- the words a player reads ---------------------------------------------------------------


def test_the_question_names_the_file_the_backup_and_the_restart(tmp_path: Path) -> None:
    _cmangos_conf(tmp_path)
    said = bot_population.question(TBC, bot_population.read(TBC, tmp_path), 50)
    assert "aiplayerbot.conf" in said and "backup" in said and "50" in said
    assert "until it is restarted" in said and "RECREATED" not in said
    assert "more than the 500" not in said


def test_a_count_above_the_installs_says_what_it_costs(tmp_path: Path) -> None:
    _cmangos_conf(tmp_path)
    said = bot_population.question(TBC, bot_population.read(TBC, tmp_path), 800)
    assert "more than the 500 Yu'lon installs" in said


def test_the_wotlk_question_asks_for_a_recreate_and_changes_only_two_lines(
    tmp_path: Path,
) -> None:
    _wotlk_stack(tmp_path)
    said = bot_population.question(WOTLK, bot_population.read(WOTLK, tmp_path), 50)
    assert "RECREATED" in said and "only those two lines change" in said
    assert "by hand" not in said and "written again" not in said


def test_each_games_installed_count_is_read_off_the_catalog() -> None:
    assert {bot_population.installed_count(e) for e in (TBC, VANILLA, TORTOISE, WOTLK)} == {500}
