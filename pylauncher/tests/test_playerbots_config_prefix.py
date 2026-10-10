"""mod-playerbots' renamed config prefix: decided per server, renamed before every start (T657).

mod-playerbots ed54b459 (#2854) renamed every key `AiPlayerbot.<Name>` -> `Playerbots.<Name>`
and reads only the new names, so a server whose module moved past it ran on the
module's defaults while Yu'lon's bot count, command-port switch and conf reads named
the old ones. Every test here goes through the path a press takes: the install's
`generate-compose` (also Update to latest's put-back), the channel's Enable, the Bots
tab's read and write, `Controller.start()` (Start, Restart, recreate), the install's
`up` and a rebuild's recreate (Rebuild, Update to latest, Return, rollback), and the
readers the Bots and Party tabs use.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.support_native import Recorder
from yulon import (
    bot_population,
    channel_setup,
    dbreads,
    docker,
    party,
    playerbots_keys,
    playerbots_rename,
    resources,
    tuning,
)
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import conf
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError
from yulon.controller import Controller, StartRefused

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
UNBOUND = CATALOG.get("wow-unbound")
OVERRIDE = composegen.OVERRIDE_FILE
CONF = playerbots_keys.CONF

# A cut of the two shipped `conf/playerbots.conf.dist` files (037c0141 / 79bd4281),
# with the three kinds of name the rename never touched.
_DIST_BODY = """\
##################################
# PLAYERBOTS CONFIGURATION
##################################
{p}Enabled = 1
{p}RandomBotAutologin = 1
{p}MinRandomBots = 500
{p}MaxRandomBots = 500
#    Default: 8888
{p}CommandServerPort = 8888
{p}RandomBotAccountPrefix = "rndbot"
{p}MaxAddedBots = 40
{p}AllowAccountBots = 1
{p}PremadeSpecName.1.0 = "arms pve"
Playerbots.Updates.EnableDatabases = 1
PlayerbotsDatabaseInfo = "127.0.0.1;3306;acore;acore;acore_playerbots"
PlayerbotsDatabase.WorkerThreads     = 1
"""
OLD_DIST = _DIST_BODY.format(p="AiPlayerbot.")
NEW_DIST = _DIST_BODY.format(p="Playerbots.")

# What a v0.9.15 server that pressed "Update to latest" has: the old names, a comment
# naming one, a player's own key, a value the player changed, CRLF on one line.
OLD_CONF = (
    "# my notes: ünïcode kept\n"
    "AiPlayerbot.Enabled = 1\n"
    "  AiPlayerbot.MinRandomBots = 50\r\n"
    "#AiPlayerbot.CommandServerPort = 8888\n"
    'AiPlayerbot.RandomBotAccountPrefix = "mybot"\n'
    "AiPlayerbot.MaxAddedBots = 12\n"
    "AiPlayerbot.AllowAccountBots = 0\n"
    'AiPlayerbot.PremadeSpecName.1.0 = "arms pve"\n'
    "AiPlayerbot.MyOwnKey = 7\n"
    "Playerbots.Updates.EnableDatabases = 1\n"
    'PlayerbotsDatabaseInfo = "x;3306;root;pw;acore_playerbots"\n'
)
NEW_CONF = OLD_CONF.replace("AiPlayerbot.", "Playerbots.")


def _linux() -> str:
    return "linux"


def lay_module(server_dir: Path, dist: str) -> None:
    path = server_dir / playerbots_keys.DIST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dist, encoding="utf-8")


def _engine(rec: Recorder | None = None, entry=WOTLK, **seams: object) -> AzerothCoreInstaller:
    made = rec.seams(platform_id=_linux, **seams) if rec is not None else None
    return AzerothCoreInstaller(
        entry,
        installers_root=resources.installers_dir(),
        seams=made
        or native.Seams(
            platform_id=_linux, selinux_enforcing=lambda: False, fs_type=lambda path: "ext4"
        ),
    )


def _context(server_dir: Path, entry=WOTLK) -> native.StageContext:
    return native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=_linux),
            family="azerothcore",
        ),
        cancel=None,
        secrets=native.Secrets(db_password=entry.install.db_password(server_dir)),
    )


def _generate(server_dir: Path, entry=WOTLK) -> str:
    """`generate-compose`: an install, a Repair, and Update to latest's put-back."""
    list(_engine(entry=entry).stage_generate_compose(_context(server_dir, entry)))
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _installed(server_dir: Path, dist: str, conf_text: str | None, entry=WOTLK) -> None:
    lay_module(server_dir, dist)
    _generate(server_dir, entry)
    if conf_text is not None:
        path = server_dir / CONF
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(conf_text.encode("utf-8"))


def _v0915_server(server_dir: Path, *, bots: int = 50) -> None:
    """Installed on the old module, bot count set, then the module moved past the rename."""
    _installed(server_dir, OLD_DIST, OLD_CONF)
    bot_population.write(WOTLK, server_dir, bots)
    for made in tuning.backups_of(server_dir / OVERRIDE):
        made.unlink()
    lay_module(server_dir, NEW_DIST)


def _start(monkeypatch: pytest.MonkeyPatch, server_dir: Path) -> tuple[Controller, list[Path]]:
    started: list[Path] = []

    def start_staged(spec: docker.ContainerSpec, where: Path, **_kw: object) -> bool:
        started.append(where)
        return True

    monkeypatch.setattr(docker, "start_staged", start_staged)
    controller = Controller(WOTLK.container_spec(), server_dir)
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    controller.start()
    return controller, started


def _world_env(text: str, entry=WOTLK) -> dict[str, str]:
    lines = text.split("\n")
    at = bot_population.bot_count.env_lines(lines, entry.container_spec().world)
    return {name: bot_population.bot_count.env_value(lines[spots[0]]) for name, spots in at.items()}


# -- the decision: read from the module checkout --------------------------------------


def test_the_prefix_is_read_from_the_modules_dist(tmp_path: Path) -> None:
    assert playerbots_keys.module_prefix(tmp_path) is None, "no checkout: no answer"
    lay_module(tmp_path, OLD_DIST)
    assert playerbots_keys.module_prefix(tmp_path) == "AiPlayerbot."
    lay_module(tmp_path, NEW_DIST)
    assert playerbots_keys.module_prefix(tmp_path) == "Playerbots."


def test_the_names_that_never_moved_are_no_evidence(tmp_path: Path) -> None:
    """`Playerbots.Updates.EnableDatabases` is in the OLD dist; it must not read as new."""
    lay_module(tmp_path, OLD_DIST)
    assert playerbots_keys.module_prefix(tmp_path) == "AiPlayerbot."
    lay_module(tmp_path, "Playerbots.Updates.EnableDatabases = 1\n")
    assert playerbots_keys.module_prefix(tmp_path) is None
    lay_module(tmp_path, OLD_DIST + "Playerbots.MinRandomBots = 1\n")
    assert playerbots_keys.module_prefix(tmp_path) is None, "both: not decided"


def test_a_dist_that_says_nothing_falls_back_to_the_config_source(tmp_path: Path) -> None:
    lay_module(tmp_path, "# nothing here\n")
    source = tmp_path / playerbots_keys.SOURCE
    source.parent.mkdir(parents=True)
    source.write_text(
        'Enabled = sConfigMgr->GetOption<bool>("Playerbots.Enabled", true);\n'
        'u = sConfigMgr->GetOption<bool>("Playerbots.Updates.EnableDatabases", true);\n',
        encoding="utf-8",
    )
    assert playerbots_keys.module_prefix(tmp_path) == "Playerbots."


@pytest.mark.parametrize(
    "key", ["AiPlayerbot.MinRandomBots", "AiPlayerbot.CommandServerPort", "AiPlayerbot.X.1.0"]
)
def test_the_environment_names_follow_azerothcores_own_rule(key: str) -> None:
    new = playerbots_keys.key(key, "Playerbots.")
    assert playerbots_keys.env(composegen.env_name_for(key), "Playerbots.") == (
        composegen.env_name_for(new)
    )
    assert playerbots_keys.env(composegen.env_name_for(new), "AiPlayerbot.") == (
        composegen.env_name_for(key)
    )


def test_the_names_that_never_moved_are_never_renamed() -> None:
    for name in playerbots_keys.NEVER_RENAMED_ENV:
        assert playerbots_keys.env(name, "AiPlayerbot.") == name
    assert playerbots_keys.env(
        composegen.env_name_for("PlayerbotsDatabaseInfo"), "AiPlayerbot."
    ) == ("AC_PLAYERBOTS_DATABASE_INFO")
    assert (
        playerbots_keys.key("Playerbots.Updates.EnableDatabases", "AiPlayerbot.")
        == "Playerbots.Updates.EnableDatabases"
    )


# -- the generated compose ---------------------------------------------------------------


@pytest.mark.parametrize("entry", [WOTLK, UNBOUND], ids=["wotlk", "unbound"])
def test_a_new_install_on_the_renamed_module_writes_the_names_it_reads(
    tmp_path: Path, entry
) -> None:
    lay_module(tmp_path, NEW_DIST)
    env = _world_env(_generate(tmp_path, entry), entry)
    assert env["AC_PLAYERBOTS_MIN_RANDOM_BOTS"] == "500"
    assert env["AC_PLAYERBOTS_MAX_RANDOM_BOTS"] == "500"
    assert env["AC_PLAYERBOTS_RANDOM_BOT_AUTOLOGIN"] == "1"
    assert env["AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES"] == "1"
    assert not [name for name in env if name.startswith("AC_AI_PLAYERBOT")], env


def test_an_install_on_the_old_module_writes_what_it_always_wrote(tmp_path: Path) -> None:
    lay_module(tmp_path, OLD_DIST)
    env = _world_env(_generate(tmp_path))
    assert env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"] == "500"
    assert env["AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES"] == "1"
    assert "AC_PLAYERBOTS_MIN_RANDOM_BOTS" not in env


def test_the_channel_switches_the_new_command_port_off(tmp_path: Path) -> None:
    lay_module(tmp_path, NEW_DIST)
    _generate(tmp_path)
    channel_setup.enable(
        WOTLK, tmp_path, templates_root=resources.installers_dir(), world_running=False
    )
    env = _world_env((tmp_path / OVERRIDE).read_text(encoding="utf-8"))
    assert env["AC_PLAYERBOTS_COMMAND_SERVER_PORT"] == "0"
    assert "AC_AI_PLAYERBOT_COMMAND_SERVER_PORT" not in env


def test_a_channel_missing_its_command_port_line_is_not_read_as_on(tmp_path: Path) -> None:
    """The channel's own lines are compared under the names the server reads."""
    lay_module(tmp_path, NEW_DIST)
    _generate(tmp_path)
    channel_setup.enable(
        WOTLK, tmp_path, templates_root=resources.installers_dir(), world_running=False
    )
    path = tmp_path / OVERRIDE
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(
            line for line in text.split("\n") if "AC_PLAYERBOTS_COMMAND_SERVER_PORT" not in line
        ),
        encoding="utf-8",
    )

    def never(*_args: object) -> object:
        raise AssertionError("asking whether it is on opens nothing")

    tab = channel_setup.InstallChannel(
        WOTLK,
        tmp_path,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(tmp_path, platform_id=_linux),
        create=never,
        channel_for=never,
        db_password=WOTLK.install.db_password(tmp_path),
    )
    assert tab.is_enabled() is not True


def test_update_to_latest_and_back_keeps_the_players_bot_count(tmp_path: Path) -> None:
    """The put-back re-render after each move carries 50 under the names the move needs."""
    _installed(tmp_path, OLD_DIST, OLD_CONF)
    bot_population.write(WOTLK, tmp_path, 50)
    lay_module(tmp_path, NEW_DIST)
    env = _world_env(_generate(tmp_path))
    assert env["AC_PLAYERBOTS_MIN_RANDOM_BOTS"] == env["AC_PLAYERBOTS_MAX_RANDOM_BOTS"] == "50"
    lay_module(tmp_path, OLD_DIST)
    env = _world_env(_generate(tmp_path))
    assert env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"] == env["AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"] == "50"
    assert "AC_PLAYERBOTS_MIN_RANDOM_BOTS" not in env


def test_the_tuning_tab_flags_a_renamed_key_the_environment_overrides(tmp_path: Path) -> None:
    lay_module(tmp_path, NEW_DIST)
    _generate(tmp_path)
    shadowed = composegen.shadowed_by_env(NEW_CONF, composegen.shadowing_env(WOTLK, tmp_path))
    assert ("Playerbots.MinRandomBots", "AC_PLAYERBOTS_MIN_RANDOM_BOTS") in shadowed


# -- the Bots tab ---------------------------------------------------------------------


def test_the_bots_tab_reads_and_writes_the_renamed_names(tmp_path: Path) -> None:
    _installed(tmp_path, NEW_DIST, NEW_CONF)
    bot_population.write(WOTLK, tmp_path, 50)
    reading = bot_population.read(WOTLK, tmp_path)
    assert (reading.problem, reading.min, reading.max) == (None, 50, 50)
    env = _world_env((tmp_path / OVERRIDE).read_text(encoding="utf-8"))
    assert env["AC_PLAYERBOTS_MIN_RANDOM_BOTS"] == "50"


def test_the_bots_tab_reads_a_server_not_renamed_yet(tmp_path: Path) -> None:
    """v0.9.15's moved server: the 50 on disk is what its next start renames, not missing."""
    _v0915_server(tmp_path)
    reading = bot_population.read(WOTLK, tmp_path)
    assert (reading.problem, reading.min, reading.max) == (None, 50, 50)
    bot_population.write(WOTLK, tmp_path, 60)
    env = _world_env((tmp_path / OVERRIDE).read_text(encoding="utf-8"))
    assert env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"] == "60", "the line that is there is changed"


def test_a_count_under_both_names_is_read_and_set_where_the_module_reads_it(tmp_path: Path) -> None:
    _installed(tmp_path, NEW_DIST, NEW_CONF)
    path = tmp_path / OVERRIDE
    lines = path.read_text(encoding="utf-8").split("\n")
    for index, line in enumerate(list(lines)):
        if "AC_PLAYERBOTS_M" in line and "RANDOM_BOTS" in line:
            lines.insert(
                index, line.replace("AC_PLAYERBOTS_", "AC_AI_PLAYERBOT_").replace("500", "7")
            )
    path.write_text("\n".join(lines), encoding="utf-8")
    assert bot_population.read(WOTLK, tmp_path).max == 500
    bot_population.write(WOTLK, tmp_path, 50)
    env = _world_env(path.read_text(encoding="utf-8"))
    assert env["AC_PLAYERBOTS_MIN_RANDOM_BOTS"] == env["AC_PLAYERBOTS_MAX_RANDOM_BOTS"] == "50"
    assert env["AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"] == "7", "the line the module ignores is left"


# -- the rename on the way to a start ------------------------------------------------


def test_start_renames_a_v0915_server_once_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _v0915_server(tmp_path)
    override_before = (tmp_path / OVERRIDE).read_bytes()

    controller, started = _start(monkeypatch, tmp_path)

    assert started == [tmp_path]
    text = (tmp_path / CONF).read_bytes().decode("utf-8")
    assert text == NEW_CONF, "every value, comment, CRLF and the player's own key kept"
    env = _world_env((tmp_path / OVERRIDE).read_text(encoding="utf-8"))
    assert env["AC_PLAYERBOTS_MIN_RANDOM_BOTS"] == env["AC_PLAYERBOTS_MAX_RANDOM_BOTS"] == "50"
    assert env["AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES"] == "1"
    assert not [name for name in env if name.startswith("AC_AI_PLAYERBOT")]
    said = controller.bot_settings_renamed
    assert said is not None and "Playerbots.*" in said and "AiPlayerbot.*" in said
    assert "playerbots.conf" in said and OVERRIDE in said
    (conf_backup,) = tuning.backups_of(tmp_path / CONF)
    assert conf_backup.read_bytes() == OLD_CONF.encode("utf-8"), "backed up first"
    (override_backup,) = tuning.backups_of(tmp_path / OVERRIDE)
    assert override_backup.read_bytes() == override_before

    again, _started = _start(monkeypatch, tmp_path)
    assert again.bot_settings_renamed is None
    assert (tmp_path / CONF).read_bytes().decode("utf-8") == NEW_CONF
    assert len(tuning.backups_of(tmp_path / CONF)) == 1, "nothing to do: nothing written"


def test_readers_read_what_the_renamed_server_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _v0915_server(tmp_path)
    _start(monkeypatch, tmp_path)
    marker = dbreads.resolve_marker(WOTLK, tmp_path)
    assert marker.marker is not None and marker.marker.prefix == "mybot"
    assert party.max_added_bots(tmp_path) == 12
    assert party.allow_flags(tmp_path).account is False
    assert party.spec_names(tmp_path) == {1: ("arms pve",)}


def test_a_server_on_the_new_module_is_read_by_its_new_keys_only(tmp_path: Path) -> None:
    """The module reads `Playerbots.*` only: an old line is not what the server has."""
    _installed(tmp_path, NEW_DIST, 'AiPlayerbot.RandomBotAccountPrefix = "mybot"\n')
    marker = dbreads.resolve_marker(WOTLK, tmp_path)
    assert (
        marker.marker is not None
        and marker.marker.prefix == WOTLK.observability.bots.account_prefix
    )


def test_return_to_the_old_pin_renames_back_on_the_rebuilds_recreate(tmp_path: Path) -> None:
    _installed(tmp_path, NEW_DIST, NEW_CONF)
    bot_population.write(WOTLK, tmp_path, 50)
    lay_module(tmp_path, OLD_DIST)  # the Return moved the checkout back
    engine = _engine(Recorder(), docker_ready=lambda: True, recreate=lambda *a, **k: True)

    said = list(engine.stage_recreate(_context(tmp_path)))

    assert (tmp_path / CONF).read_bytes().decode("utf-8") == OLD_CONF
    env = _world_env((tmp_path / OVERRIDE).read_text(encoding="utf-8"))
    assert env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"] == env["AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"] == "50"
    assert any("AiPlayerbot.*" in line and "renamed" in line for line in said), said


def test_a_rollback_renames_back_before_the_build_from_before_starts(tmp_path: Path) -> None:
    _v0915_server(tmp_path)
    list(
        _engine(
            Recorder(), docker_ready=lambda: True, recreate=lambda *a, **k: True
        ).stage_recreate(_context(tmp_path))
    )
    assert (tmp_path / CONF).read_bytes().decode("utf-8") == NEW_CONF
    lay_module(tmp_path, OLD_DIST)  # the rollback put the old sources back
    said = list(
        _engine(
            Recorder(), docker_ready=lambda: True, recreate=lambda *a, **k: True
        ).stage_recreate(_context(tmp_path), rollback=True)
    )
    assert (tmp_path / CONF).read_bytes().decode("utf-8") == OLD_CONF
    assert any("AiPlayerbot.*" in line for line in said), said


def test_the_installs_up_renames_before_it_starts(tmp_path: Path) -> None:
    _v0915_server(tmp_path)
    rec = Recorder()
    said = list(_engine(rec).stage_up(_context(tmp_path)))
    assert (tmp_path / CONF).read_bytes().decode("utf-8") == NEW_CONF
    assert any("Playerbots.*" in line for line in said), said


def test_a_rename_that_cannot_be_written_stops_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _v0915_server(tmp_path)

    def refuse(path: Path, text: str, *, mode: int | None = None) -> None:
        raise InstallerError(f"{path.name} could not be written (disk full)")

    monkeypatch.setattr(conf, "replace_file", refuse)
    with pytest.raises(StartRefused, match="not started"):
        _start(monkeypatch, tmp_path)
    with pytest.raises(InstallerError, match="not started"):
        list(_engine(Recorder()).stage_up(_context(tmp_path)))


def test_a_key_set_under_both_names_keeps_the_one_the_module_reads() -> None:
    text = "Playerbots.MinRandomBots = 9\nAiPlayerbot.MinRandomBots = 50\n"
    renamed, count = playerbots_rename.rename_conf_text(text, "Playerbots.")
    assert (renamed, count) == (text, 0)
    renamed, count = playerbots_rename.rename_conf_text(renamed, "Playerbots.")
    assert count == 0


def test_the_channels_copy_is_renamed_too_without_a_backup_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed(tmp_path, OLD_DIST, OLD_CONF)
    channel_setup.enable(
        WOTLK, tmp_path, templates_root=resources.installers_dir(), world_running=False
    )
    lay_module(tmp_path, NEW_DIST)
    _start(monkeypatch, tmp_path)
    copy = tmp_path / f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}"
    env = _world_env(copy.read_text(encoding="utf-8"))
    assert env["AC_PLAYERBOTS_MIN_RANDOM_BOTS"] == "500"
    assert not [name for name in env if name.startswith("AC_AI_PLAYERBOT")]
    assert not tuning.backups_of(copy)
    assert all(
        made.name.count(".before-channel") == 0 for made in tuning.backups_of(tmp_path / OVERRIDE)
    )


def test_a_cmangos_server_is_never_renamed(tmp_path: Path) -> None:
    tbc = CATALOG.get("wow-tbc")
    lay_module(tmp_path, NEW_DIST)
    (tmp_path / CONF).parent.mkdir(parents=True)
    (tmp_path / CONF).write_bytes(OLD_CONF.encode("utf-8"))
    assert playerbots_rename.settle(tbc, tmp_path) is None
    assert (tmp_path / CONF).read_bytes() == OLD_CONF.encode("utf-8")


# -- the binary reading the Party tab trusts ---------------------------------------------


@pytest.mark.parametrize("built", ["AiPlayerbot.Enabled", "Playerbots.Enabled"])
def test_the_engine_reading_knows_a_build_on_either_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built: str
) -> None:
    monkeypatch.setattr(party.platform, "docker_prefix", lambda _distro: ["docker"])
    binary = tmp_path / "worldserver"
    binary.write_bytes(b"\x00junk\x00" + built.encode() + b"\x00ALE.ScriptPath\x00")

    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", "-c", argv[-1]], capture_output=True, text=True, check=False)

    read = party.read_engine_in_binary("ac-worldserver", binary=str(binary), run=run)
    assert read.engine is True, read
