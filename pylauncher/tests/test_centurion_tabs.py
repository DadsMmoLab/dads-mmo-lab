"""Every tab's seam answers a `trinitycore` server (T179 Task 5).

Each test here is one family-decision site (`catalog/families/decisions.py`) that
was `pending: Task 5`, now serving the family. The entry is the Centurion-shaped
fixture (`support_trinitycore.centurion_like()`): the shipped `wow-centurion`
entry is Task 7's. Where a CMaNGOS game has the same feature, the test compares
Centurion's answer with the INSTALL's own output rather than a copy of the rule.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from tests.support_trinitycore import FakeMmapsDocker, centurion_like
from yulon import bot_population, docker, install_wiring, reset_defaults, tuning
from yulon.catalog import composegen, native, time_zone
from yulon.catalog.families import azerothcore, decisions
from yulon.catalog.families.cmangos import ETC_DIR
from yulon.catalog.families.trinitycore import TrinityCoreInstaller
from yulon.controller_wow_centurion import controller as centurion_controller
from yulon.controller_wow_centurion import docker_ctl as centurion_docker

ENTRY = centurion_like()
PLAYERBOTS = f"{ETC_DIR}/playerbots.conf"


def _linux() -> str:
    return "linux"


# -- Tuning: time zone ---------------------------------------------------------------


def test_the_time_zone_goes_on_the_login_and_the_world_server() -> None:
    spec = ENTRY.container_spec()
    assert time_zone.services(ENTRY) == (spec.auth, spec.world)
    assert time_zone.line_for(ENTRY, "Europe/Oslo") is not None


def test_the_trinitycore_image_brings_no_zone_files_so_the_server_folder_does() -> None:
    """The runtime stage is `ubuntu:24.04` with no `tzdata`, as the CMaNGOS images are."""
    dockerfile = (
        Path(native.__file__).parents[2] / "catalog/installers/wow-centurion/native/Dockerfile.tmpl"
    ).read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM ", 2)[2]
    assert "tzdata" not in runtime
    assert time_zone.needs_files(ENTRY) is True


def test_the_controller_puts_the_zone_file_back_from_its_own_entry(tmp_path: Path) -> None:
    """`Controller._put_back_the_zone_file` read the SHIPPED catalog by container names;
    the Centurion controller carries its entry, which that catalog does not have yet."""
    made = centurion_controller.CenturionController(ENTRY, tmp_path)
    assert made.entry is ENTRY
    world = ENTRY.container_spec().world
    (tmp_path / composegen.OVERRIDE_FILE).write_text(
        f'services:\n  {world}:\n    environment:\n      TZ: "Europe/Oslo"\n', encoding="utf-8"
    )
    assert made._put_back_the_zone_file() is None
    assert (tmp_path / time_zone.FOLDER / "Europe" / "Oslo").is_file()


# -- Bots: the random-bot count in playerbots.conf -------------------------------------


def _playerbots(server: Path, low: str = "150", high: str = "150") -> Path:
    path = server / PLAYERBOTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Playerbot.Enable = 1\n"
        "Playerbot.RandomPopulation.Enable = 1\n"
        f"Playerbot.RandomPopulation.TargetMin = {low}\n"
        f"Playerbot.RandomPopulation.TargetMax = {high}\n"
        "Playerbot.RandomPopulation.BotAccountIds = 76,77,78\n",
        encoding="utf-8",
    )
    return path


def test_the_bot_count_lives_in_playerbots_conf_beside_worldserver_conf() -> None:
    assert bot_population.where(ENTRY) == (PLAYERBOTS, "conf")
    assert bot_population.installed_count(ENTRY) == 150


def test_the_bot_count_reads_and_writes_target_min_and_max(tmp_path: Path) -> None:
    path = _playerbots(tmp_path)
    reading = bot_population.read(ENTRY, tmp_path)
    assert reading.problem is None
    assert (reading.min, reading.max) == (150, 150)
    assert reading.ceiling == bot_population.NO_CEILING
    assert reading.ceiling_why == bot_population.CEILING_NONE

    written = bot_population.write(ENTRY, tmp_path, 40)

    assert written.changed and written.rule == tuning.file_rule(PLAYERBOTS)
    text = path.read_text(encoding="utf-8")
    assert "Playerbot.RandomPopulation.TargetMin = 40\n" in text
    assert "Playerbot.RandomPopulation.TargetMax = 40\n" in text
    assert "Playerbot.RandomPopulation.BotAccountIds = 76,77,78\n" in text
    assert "AiPlayerbot" not in text


def test_the_tuning_card_rows_are_the_playerbots_conf_keys_the_install_writes(
    tmp_path: Path,
) -> None:
    _playerbots(tmp_path, "10", "20")
    rows = bot_population.read(ENTRY, tmp_path).rows
    assert {row.key for row in rows} == set(
        ENTRY.install.native.trinitycore.conf.files["playerbots.conf"].keys  # type: ignore[union-attr]
    )
    assert {row.file for row in rows} == {PLAYERBOTS}
    counted = {row.key: row for row in rows if row.type == "int"}
    assert set(counted) == {
        "Playerbot.RandomPopulation.TargetMin",
        "Playerbot.RandomPopulation.TargetMax",
    }
    assert counted["Playerbot.RandomPopulation.TargetMax"].current == "20"
    assert bot_population.card_file(ENTRY) == PLAYERBOTS
    assert set(bot_population.conf_keys(ENTRY)) == {row.key for row in rows}


def test_a_cmangos_game_keeps_its_own_bot_conf() -> None:
    from yulon.catalog.catalog import load_catalog

    tbc = load_catalog().get("wow-tbc")
    assert bot_population.where(tbc) == ("etc/aiplayerbot.conf", "conf")
    assert bot_population.card_file(tbc) == "etc/aiplayerbot.conf"


def test_the_bot_marker_is_the_fixed_account_names_with_no_setting_to_read(
    tmp_path: Path,
) -> None:
    """PLAYERBOTONE..FOUR (`auth_bots.sql`): no prefix key exists, so nothing is read."""
    from yulon import dbreads

    answer = dbreads.resolve_marker(ENTRY, tmp_path / "nowhere")
    assert answer.problem == ""
    assert answer.marker == dbreads.Marker("PLAYERBOT", "default")
    clause = dbreads.bot_clause(ENTRY, answer.marker)
    assert f"{ENTRY.databases.auth}.account" in clause and "'PLAYERBOT%'" in clause


def test_a_bot_marker_names_its_conf_file_and_key_together() -> None:
    from pydantic import ValidationError

    from yulon.catalog.catalog import BotMarker

    BotMarker(account_prefix="rndbot", prefix_conf_file="etc/a.conf", prefix_conf_key="K")
    BotMarker(account_prefix="PLAYERBOT")
    with pytest.raises(ValidationError, match="go together"):
        BotMarker(account_prefix="rndbot", prefix_conf_file="etc/a.conf")
    with pytest.raises(ValidationError, match="go together"):
        BotMarker(account_prefix="rndbot", prefix_conf_key="K")


# -- Tuning: Reset to default from the image's .dist -----------------------------------


TEMPLATES = {
    "worldserver.conf.dist": (
        'DataDir = "."\nUpdates.EnableDatabases = 7\nWorldServerPort = 8085\nSOAP.Enabled = 0\n'
    ),
    "authserver.conf.dist": 'LoginDatabaseInfo = "127.0.0.1;3306;trinity;trinity;auth"\n',
    "playerbots.conf.dist": "Playerbot.Enable = 0\r\nPlayerbot.RandomPopulation.TargetMin = 0\r\n",
}


class _FakeImage:
    def __init__(self) -> None:
        self.copies: list[tuple[str, str]] = []

    def __call__(self, image: str, src: str, dest: Path) -> None:
        self.copies.append((image, src))
        for name, text in TEMPLATES.items():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))


def _server(tmp_path: Path) -> tuple[Path, str]:
    server = tmp_path / "centurion"
    server.mkdir()
    password = "tc-" + secrets.token_hex(8)
    (server / ".db_password").write_text(password + "\n", encoding="utf-8")
    return server, password


def test_the_files_reset_to_default_offers_are_the_install_conf_table() -> None:
    assert reset_defaults.core_files(ENTRY) == (
        "etc/worldserver.conf",
        "etc/authserver.conf",
        "etc/playerbots.conf",
    )
    for file in reset_defaults.core_files(ENTRY):
        assert reset_defaults.install_writes(ENTRY, file) is True
    assert "updates.enabledatabases" in reset_defaults.install_keys(ENTRY, "etc/worldserver.conf")
    assert reset_defaults.install_keys(ENTRY, "etc/playerbots.conf") == frozenset(
        key.casefold()
        for key in ENTRY.install.native.trinitycore.conf.files["playerbots.conf"].keys  # type: ignore[union-attr]
    )


def _engine() -> TrinityCoreInstaller:
    return TrinityCoreInstaller(
        ENTRY,
        seams=native.Seams(platform_id=_linux, copy_from_image=_FakeImage()),
        mmaps_runner=FakeMmapsDocker(),
    )


def _context(server: Path, password: str, *, done: tuple[str, ...] = ()) -> native.StageContext:
    return native.StageContext(
        server_dir=server,
        client_dir=None,
        state=native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server, platform_id=_linux),
            family="trinitycore",
            completed=done,
        ),
        cancel=None,
        secrets=native.Secrets(db_password=password),
    )


def test_the_conf_stage_run_again_keeps_the_players_bot_count(tmp_path: Path) -> None:
    """T117's carry-over, for this family's `playerbots.conf` (Task 3 left it to Task 5)."""
    server, password = _server(tmp_path)
    engine = _engine()
    list(engine._conf(_context(server, password)))
    path = server / PLAYERBOTS
    assert "Playerbot.RandomPopulation.TargetMax = 150" in path.read_text(encoding="utf-8")
    bot_population.write(ENTRY, server, 40)

    list(engine._conf(_context(server, password, done=("conf",))))

    text = path.read_text(encoding="utf-8")
    assert "Playerbot.RandomPopulation.TargetMin = 40" in text
    assert "Playerbot.RandomPopulation.TargetMax = 40" in text
    # A first run (the stage never finished) takes the table's, not the file's.
    list(engine._conf(_context(server, password)))
    assert "Playerbot.RandomPopulation.TargetMax = 150" in path.read_text(encoding="utf-8")


def test_the_default_of_each_file_is_what_the_install_itself_wrote(tmp_path: Path) -> None:
    """The install's own conf stage over the same image templates, byte for byte."""
    server, password = _server(tmp_path)
    list(_engine()._conf(_context(server, password)))
    files = reset_defaults.core_files(ENTRY)
    installed = {file: (server / file).read_bytes() for file in files}
    image = _FakeImage()

    texts, reasons = reset_defaults.default_texts(
        ENTRY,
        server,
        files,
        seams=reset_defaults.Seams(
            copy_from_image=image,
            image_present=lambda refs: True,
            platform_id=_linux,
            bind_label=lambda server_dir: "",
        ),
    )

    assert reasons == {}
    assert {file: text.encode("utf-8") for file, text in texts.items()} == installed
    assert len(image.copies) == 1
    world = texts["etc/worldserver.conf"]
    assert "Updates.EnableDatabases = 0" in world
    assert f";{password};" in world
    assert "SOAP.Enabled = 1" in world


def test_the_rebuild_confirmation_names_reset_to_default_where_it_reads_the_image() -> None:
    assert "Reset to default" in native.no_rollback_confirmation(ENTRY)


# -- Server tab: Repair server files -----------------------------------------------------


def test_repair_server_files_is_offered_for_the_compose_and_not_for_dist_confs(
    tmp_path: Path,
) -> None:
    route = install_wiring.repair_compose_for_app(ENTRY, tmp_path)
    assert route is not None
    assert install_wiring.repair_compose_for_app(ENTRY, tmp_path, wsl_distro="Ubuntu") is None
    # Every conf is the conf table's, playerbots.conf included: no `.dist` copy to repair.
    assert azerothcore.confs_from_dist(ENTRY) == ()
    assert install_wiring.repair_confs_for_app(ENTRY, tmp_path) is None


def test_the_compose_check_reads_this_familys_own_render(tmp_path: Path) -> None:
    """No compose file: `missing`. The install's own render: `current`. Edited: `stale`."""
    server, password = _server(tmp_path)
    route = install_wiring.repair_compose_for_app(ENTRY, server)
    assert route is not None
    assert route.check().state == "missing"

    list(_engine().stage_generate_compose(_context(server, password)))
    assert route.check().state == "current", route.check()

    base = server / composegen.BASE_FILE
    text = base.read_text(encoding="utf-8")
    assert text.count("stdin_open: true") == 1
    base.write_text(text.replace("stdin_open: true", "stdin_open: false"), encoding="utf-8")
    assert route.check().state == "stale", route.check()


# -- Server tab: when is the server up -----------------------------------------------------


def test_the_ready_wait_is_the_entrys_marker_and_never_azerothcores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[docker.ReadySpec] = []

    def wait(spec: docker.ContainerSpec, ready: docker.ReadySpec, **kwargs: object) -> bool:
        assert spec == ENTRY.container_spec()
        seen.append(ready)
        return True

    monkeypatch.setattr(native, "wait_ready_quietly", wait)
    made = centurion_controller.CenturionController(ENTRY, tmp_path)
    assert made.wait_ready() is True
    (ready,) = seen
    assert ready.world == "World\\ initialized"
    assert ready.auth is None
    assert "ready" not in ready.world


def test_a_ready_marker_with_a_token_the_controller_cannot_fill_is_refused() -> None:
    data = ENTRY.model_dump()
    data["install"]["native"]["ready"] = {"world": "{{REALM_HOST}} up"}
    broken = type(ENTRY).model_validate(data)
    with pytest.raises(ValueError, match="only the install"):
        centurion_docker.ready_spec(broken)


def test_the_centurion_package_refuses_an_entry_of_another_family() -> None:
    from yulon.catalog.catalog import load_catalog

    with pytest.raises(RuntimeError, match="not a TrinityCore install"):
        centurion_controller.CenturionController(load_catalog().get("wow-tbc"), Path("x"))


# -- the registry: nothing is left for Task 5 ----------------------------------------------


def test_no_family_decision_is_still_pending_for_task_5() -> None:
    left = [
        f"{site.module}:{site.scope}"
        for site in decisions.FAMILY_DECISIONS
        for decision in site.decisions.values()
        if decision.kind == "pending" and decision.note.startswith("Task 5")
    ]
    assert left == []
