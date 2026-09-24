"""T94: put a server's own settings files back to how Yu'lon installed them.

"Default" here is not the upstream file. For the three CMaNGOS games it is the
image's template with the install's own conf table applied with the install's
own tokens -- the database logins, the world port, SOAP on, the bot
population -- because a raw `.dist` would lock the server out of its database.
For WotLK the confs' default is the `.dist` beside each, because that install
writes no conf keys; its settings live in `docker-compose.override.yml`, whose
default is what the install's compose stage renders. Every test here that could
compare the reset against a copy of its own logic compares it against the
INSTALL instead.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

from yulon import channel_setup, dbsecret, platform, reset_defaults, resources, tuning
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import conf
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.families.cmangos import ETC_DIR, CmangosInstaller
from yulon.catalog.installer import InstallerError
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.manifest_store import FAMILY_FILES, ManifestStore

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
VANILLA = CATALOG.get("wow-vanilla")
TORTOISE = CATALOG.get("wow-tortoise")
OVERRIDE = composegen.OVERRIDE_FILE


def _linux() -> str:
    return "linux"


def _context(
    server_dir: Path, password: str, entry: object = TBC, family: str = "cmangos"
) -> native.StageContext:
    """The context a stage body is handed, for the one install at `server_dir`."""
    return native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=entry.id,  # type: ignore[attr-defined]
            install_id=composegen.install_id(server_dir, platform_id=_linux),
            family=family,
        ),
        cancel=None,
        secrets=native.Secrets(db_password=password),
    )


# -- which files are a game's own -------------------------------------------


def test_each_games_files_are_its_install_conf_table_or_wotlks_three_and_its_override() -> None:
    """Written out, not derived: the list IS the claim (spec corrections 2, 17)."""
    cmangos = ("etc/mangosd.conf", "etc/realmd.conf", "etc/aiplayerbot.conf", "etc/ahbot.conf")
    assert reset_defaults.core_files(TBC) == cmangos
    assert reset_defaults.core_files(VANILLA) == cmangos
    assert reset_defaults.core_files(TORTOISE) == (
        "etc/aiplayerbot.conf",
        "etc/modules/tortoise_bots.conf",
        "etc/mangosd.conf",
        "etc/realmd.conf",
    )
    assert reset_defaults.AZEROTHCORE_CORE_FILES == (
        "env/dist/etc/worldserver.conf",
        "env/dist/etc/authserver.conf",
        "env/dist/etc/modules/playerbots.conf",
    )
    assert reset_defaults.core_files(WOTLK) == (
        *reset_defaults.AZEROTHCORE_CORE_FILES,
        "docker-compose.override.yml",
    )


def test_a_label_is_the_path_under_the_games_own_etc_folder() -> None:
    assert reset_defaults.label("etc/mangosd.conf") == "mangosd.conf"
    assert reset_defaults.label("etc/modules/tortoise_bots.conf") == "modules/tortoise_bots.conf"
    assert reset_defaults.label("env/dist/etc/worldserver.conf") == "worldserver.conf"
    assert reset_defaults.label("env/dist/etc/modules/playerbots.conf") == "modules/playerbots.conf"
    assert reset_defaults.label(OVERRIDE) == "docker-compose.override.yml"


# -- the engine's seams: one body, two callers --------------------------------


def test_conf_tokens_is_the_conf_stages_own_mapping(tmp_path: Path) -> None:
    password = "tbc-" + secrets.token_hex(8)
    engine = CmangosInstaller(TBC, seams=native.Seams(platform_id=_linux))
    tokens = engine.conf_tokens(tmp_path, native.Secrets(db_password=password))
    assert tokens == engine._secret_tokens(_context(tmp_path, password))
    assert tokens["DB_PASSWORD"] == password
    assert "DB_PASSWORD" not in engine._public_tokens(tmp_path)


def test_conf_image_ref_is_the_image_the_conf_stage_copies_from(tmp_path: Path) -> None:
    engine = CmangosInstaller(TBC, seams=native.Seams(platform_id=_linux))
    data = TBC.install.native.cmangos  # type: ignore[union-attr]
    stage = engine._image_ref(_context(tmp_path, "x"), data.extract.image)
    assert engine.conf_image_ref(tmp_path) == stage
    assert stage.startswith(f"{TBC.install.native.image_prefix}{data.extract.image}:")  # type: ignore[union-attr]


def test_conf_table_is_the_entrys_own() -> None:
    engine = CmangosInstaller(TORTOISE, seams=native.Seams(platform_id=_linux))
    assert engine.conf_table() == TORTOISE.install.native.cmangos.conf  # type: ignore[union-attr]


def test_replace_file_keeps_the_text_exactly_and_leaves_nothing_beside_it(tmp_path: Path) -> None:
    path = tmp_path / "mangosd.conf"
    path.write_bytes(b"A = 1\r\n")
    conf.replace_file(path, "A = 2\r\nB = 3\n")
    assert path.read_bytes() == b"A = 2\r\nB = 3\n"
    assert [p.name for p in tmp_path.iterdir()] == ["mangosd.conf"]
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == conf.CONF_MODE


# -- the image, faked the way `docker cp` behaves ------------------------------

TEMPLATES: dict[str, dict[str, str]] = {
    "wow-tbc": {
        "mangosd.conf.dist": 'LoginDatabaseInfo = "old"\nWorldServerPort = 1\nRate.XP.Kill = 1\n',
        "realmd.conf.dist": 'LoginDatabaseInfo = "old"\n',
        # CRLF, as `playerbots`' real `aiplayerbot.conf.dist.in` is (conf.py:46-52).
        "aiplayerbot.conf.dist": "AiPlayerbot.MinRandomBots = 50\r\nAiPlayerbot.Other = 1\r\n",
        "ahbot.conf.dist": "AuctionHouseBot.Chance.Sell = 0\n",
    },
    "wow-tortoise": {
        "mangosd.conf.dist": 'LoginDatabase.Info = "old"\nConsole.Enable = 0\n',
        "realmd.conf.dist": 'LoginDatabaseInfo = "old"\n',
        # No `.dist` in the Penqle image: the template override names the live file.
        "aiplayerbot.conf": "AiPlayerbot.Enabled = 0\r\n",
        "modules/tortoise_bots.conf.dist": "TortoiseBots.LogLevel = 3\n",
    },
}
TEMPLATES["wow-vanilla"] = {
    **TEMPLATES["wow-tbc"],
    "aiplayerbot.conf.dist": (
        "# AiPlayerbot.SyncLevelWithPlayers = 0\r\nAiPlayerbot.MinRandomBots = 50\r\n"
    ),
}


class FakeImage:
    """`docker.copy_from_image` over a dict: `dest` does not exist, so it BECOMES the folder."""

    def __init__(self, templates: dict[str, str]) -> None:
        self.templates = templates
        self.copies: list[tuple[str, str]] = []

    def __call__(self, image: str, src: str, dest: Path) -> None:
        self.copies.append((image, src))
        for name, text in self.templates.items():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))


def _never(*args: object, **kwargs: object) -> None:
    raise AssertionError(f"docker was reached: {args}")


def _seams(image: object = _never, **overrides: object) -> reset_defaults.Seams:
    base: dict[str, object] = {
        "copy_from_image": image,
        "image_present": lambda refs: True,
        "platform_id": _linux,
        "bind_label": lambda server_dir: "",
    }
    base.update(overrides)
    return reset_defaults.Seams(**base)  # type: ignore[arg-type]


def _server(tmp_path: Path, game: str) -> tuple[Path, str]:
    """A server folder with the generated password file a CMaNGOS install leaves."""
    server = tmp_path / game
    server.mkdir()
    password = game.removeprefix("wow-") + "-" + secrets.token_hex(8)
    (server / ".db_password").write_text(password + "\n", encoding="utf-8")
    return server, password


def _fresh_install(game: str, server: Path, image: FakeImage) -> dict[str, bytes]:
    """What the INSTALL's own conf stage writes into `server`, through its real body."""
    entry = CATALOG.get(game)
    engine = CmangosInstaller(entry, seams=native.Seams(platform_id=_linux, copy_from_image=image))
    password = (server / ".db_password").read_text(encoding="utf-8").strip()
    list(engine._conf(_context(server, password, entry)))
    return {file: (server / file).read_bytes() for file in reset_defaults.core_files(entry)}


def _wotlk_stack(server: Path, *, channel: bool = False) -> bytes:
    """The override the install's OWN compose stage writes (and the channel press, if asked)."""
    engine = AzerothCoreInstaller(
        WOTLK,
        seams=native.Seams(
            platform_id=_linux, selinux_enforcing=lambda: False, fs_type=lambda path: "ext4"
        ),
    )
    list(engine.stage_generate_compose(_context(server, "password", WOTLK, "azerothcore")))
    if channel:
        channel_setup.enable(
            WOTLK, server, templates_root=resources.installers_dir(), world_running=False
        )
    return (server / OVERRIDE).read_bytes()


# -- spec Test 1: the default IS what a fresh install writes -------------------


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise"])
def test_the_default_of_each_file_is_what_the_install_itself_wrote(
    tmp_path: Path, game: str
) -> None:
    server, password = _server(tmp_path, game)
    installed = _fresh_install(game, server, FakeImage(TEMPLATES[game]))
    image = FakeImage(TEMPLATES[game])

    texts, reasons = reset_defaults.default_texts(
        CATALOG.get(game), server, list(installed), seams=_seams(image)
    )

    assert reasons == {}
    assert {file: text.encode("utf-8") for file, text in texts.items()} == installed
    assert len(image.copies) == 1, "one docker cp for every file, as the install does"
    entry = CATALOG.get(game)
    mangosd = texts["etc/mangosd.conf"]
    assert f"{entry.containers.db};3306;" in mangosd and f";{password};" in mangosd
    assert f"WorldServerPort = {entry.ports.world}" in mangosd


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise"])
def test_a_reset_never_switches_the_cmangos_command_channel_off(tmp_path: Path, game: str) -> None:
    """Spec correction 12: every key the Enable press writes is in the default too."""
    entry = CATALOG.get(game)
    server, _password = _server(tmp_path, game)
    texts, _ = reset_defaults.default_texts(
        entry, server, ["etc/mangosd.conf"], seams=_seams(FakeImage(TEMPLATES[game]))
    )
    assert entry.operations is not None and entry.operations.enable_conf is not None
    for key, value in entry.operations.enable_conf.keys.items():
        assert f"{key} = {value}" in texts["etc/mangosd.conf"], key


def test_a_crlf_template_stays_crlf_and_an_appended_key_takes_its_ending(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, "wow-tbc")
    texts, _ = reset_defaults.default_texts(
        TBC, server, ["etc/aiplayerbot.conf"], seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )
    text = texts["etc/aiplayerbot.conf"]
    assert "AiPlayerbot.RandomBotAccountCount = 100\r\n" in text  # appended: absent in template
    assert "\n" not in text.replace("\r\n", ""), "a bare LF crept into a CRLF file"


# -- spec Test 2: WotLK's confs default to their .dist siblings -----------------


def _dist_install(server: Path) -> dict[str, bytes]:
    """The three WotLK core confs, tuned, each with a `.dist` beside it (non-ASCII, CRLF)."""
    defaults: dict[str, bytes] = {}
    for file in reset_defaults.AZEROTHCORE_CORE_FILES:
        path = server / file
        path.parent.mkdir(parents=True, exist_ok=True)
        defaults[file] = f"# {path.name} — as shipped\r\nKey = 1\r\n".encode()
        path.with_name(path.name + conf.DIST_SUFFIX).write_bytes(defaults[file])
        path.write_bytes(b"Key = 2\n")
    return defaults


def test_wotlk_conf_default_is_the_dist_sibling_byte_for_byte_and_asks_no_docker(
    tmp_path: Path,
) -> None:
    defaults = _dist_install(tmp_path)
    texts, reasons = reset_defaults.default_texts(
        WOTLK,
        tmp_path,
        reset_defaults.AZEROTHCORE_CORE_FILES,
        seams=_seams(image_present=_never),
    )
    assert reasons == {}
    assert {file: text.encode("utf-8") for file, text in texts.items()} == defaults


def test_a_missing_dist_is_a_reason_naming_it(tmp_path: Path) -> None:
    _dist_install(tmp_path)
    (tmp_path / "env/dist/etc/authserver.conf.dist").unlink()
    texts, reasons = reset_defaults.default_texts(
        WOTLK, tmp_path, reset_defaults.AZEROTHCORE_CORE_FILES, seams=_seams()
    )
    assert set(reasons) == {"env/dist/etc/authserver.conf"}
    assert "authserver.conf.dist" in reasons["env/dist/etc/authserver.conf"]


# -- owner decision 4: WotLK's compose override -------------------------------


def test_the_wotlk_override_default_is_what_the_install_itself_wrote(tmp_path: Path) -> None:
    installed = _wotlk_stack(tmp_path)
    (tmp_path / OVERRIDE).write_text(
        'services:\n  ac-worldserver:\n    environment:\n      TZ: "Mars/Olympus"\n',
        encoding="utf-8",
    )
    texts, reasons = reset_defaults.default_texts(
        WOTLK, tmp_path, [OVERRIDE], seams=_seams(image_present=_never)
    )
    assert reasons == {}
    assert texts[OVERRIDE].encode("utf-8") == installed
    assert 'AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "500"' in texts[OVERRIDE]
    assert "TZ" not in texts[OVERRIDE]


def test_a_live_channel_press_is_reapplied_so_a_reset_never_turns_soap_off(
    tmp_path: Path,
) -> None:
    """Correction 19: the press's env is kept while the press is live."""
    pressed = _wotlk_stack(tmp_path, channel=True)
    assert b"AC_SOAP_ENABLED" in pressed, "control: the press wrote the channel env"
    assert reset_defaults.channel_is_on(tmp_path)
    (tmp_path / OVERRIDE).write_text("services: [broken\n", encoding="utf-8")
    texts, _ = reset_defaults.default_texts(WOTLK, tmp_path, [OVERRIDE], seams=_seams())
    assert texts[OVERRIDE].encode("utf-8") == pressed


def test_after_the_channel_is_rolled_back_the_default_has_no_channel_env(tmp_path: Path) -> None:
    installed = _wotlk_stack(tmp_path)
    channel_setup.enable(
        WOTLK, tmp_path, templates_root=resources.installers_dir(), world_running=False
    )
    channel_setup.roll_back(WOTLK, tmp_path)
    assert not reset_defaults.channel_is_on(tmp_path)
    texts, _ = reset_defaults.default_texts(WOTLK, tmp_path, [OVERRIDE], seams=_seams())
    assert texts[OVERRIDE].encode("utf-8") == installed


def test_the_override_takes_the_bind_label_the_install_would_take_here(tmp_path: Path) -> None:
    _wotlk_stack(tmp_path)
    texts, _ = reset_defaults.default_texts(
        WOTLK, tmp_path, [OVERRIDE], seams=_seams(bind_label=lambda server_dir: ":z")
    )
    assert "./modules:/azerothcore/modules:z" in texts[OVERRIDE]


# -- every reason a CMaNGOS default cannot be built ----------------------------


def test_an_image_no_longer_on_the_machine_says_rebuild_first(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, "wow-tbc")
    files = reset_defaults.core_files(TBC)
    texts, reasons = reset_defaults.default_texts(
        TBC, server, files, seams=_seams(_never, image_present=lambda refs: False)
    )
    assert texts == {} and set(reasons) == set(files)
    assert all("rebuild the server first" in reason for reason in reasons.values())


def test_docker_not_answering_is_its_own_reason(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, "wow-tbc")
    _, reasons = reset_defaults.default_texts(
        TBC, server, ["etc/realmd.conf"], seams=_seams(_never, image_present=lambda refs: None)
    )
    assert "Docker did not answer" in reasons["etc/realmd.conf"]


def test_an_unreadable_password_refuses_before_docker_is_asked(tmp_path: Path) -> None:
    """Spec correction 3: never mint one -- that would lock the server out of its database."""
    server, _ = _server(tmp_path, "wow-tbc")
    (server / ".db_password").unlink()
    _, reasons = reset_defaults.default_texts(
        TBC, server, ["etc/mangosd.conf"], seams=_seams(_never, image_present=_never)
    )
    assert ".db_password" in reasons["etc/mangosd.conf"]


def test_a_cmangos_server_inside_a_wsl_distro_is_refused(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, "wow-tbc")
    _, reasons = reset_defaults.default_texts(
        TBC, server, ["etc/mangosd.conf"], wsl_distro="Ubuntu", seams=_seams(_never)
    )
    assert "Ubuntu" in reasons["etc/mangosd.conf"]


def test_a_template_the_image_does_not_ship_is_a_reason_naming_it(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, "wow-tbc")
    templates = dict(TEMPLATES["wow-tbc"])
    del templates["ahbot.conf.dist"]
    texts, reasons = reset_defaults.default_texts(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(templates))
    )
    assert set(reasons) == {"etc/ahbot.conf"}
    assert "/opt/mangos/etc/ahbot.conf.dist" in reasons["etc/ahbot.conf"]
    assert set(texts) == {"etc/mangosd.conf", "etc/realmd.conf", "etc/aiplayerbot.conf"}


@pytest.mark.parametrize("seam", ["conf_image_ref", "conf_tokens"])
def test_a_catalog_the_engine_refuses_is_a_reason_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    """The engine's own refusals (`InstallerError`) become reasons: `reset()` catches none."""

    def refuse(*args: object, **kwargs: object) -> str:
        raise InstallerError(f"{seam} refused: a bug in the app")

    monkeypatch.setattr(CmangosInstaller, seam, refuse)
    server, _ = _server(tmp_path, "wow-tbc")
    files = reset_defaults.core_files(TBC)
    texts, reasons = reset_defaults.default_texts(
        TBC, server, files, seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )
    assert texts == {}
    assert reasons == dict.fromkeys(files, f"{seam} refused: a bug in the app")


# -- the lead's rulings on Task 2 (review of f9163dee) -------------------------


def test_a_password_file_that_is_gone_takes_the_copy_yulon_kept_like_the_install(
    tmp_path: Path,
) -> None:
    """The install's own rule (`native.py` `_secrets`): absent file -> the kept copy."""
    server, _ = _server(tmp_path, "wow-tbc")
    (server / ".db_password").unlink()
    kept = "tbc-" + secrets.token_hex(8)
    dbsecret.remember(
        TBC.id,
        composegen.install_id(server, platform_id=_linux),
        password=kept,
        volume="tbc-db-data",
        config_dir=platform.config_dir(),
    )
    texts, reasons = reset_defaults.default_texts(
        TBC, server, ["etc/mangosd.conf"], seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )
    assert reasons == {}
    assert f";{kept};" in texts["etc/mangosd.conf"]


@pytest.mark.parametrize("state", ["not utf-8", "empty", "a folder"])
def test_a_password_file_that_is_there_but_unusable_refuses_even_with_a_kept_copy(
    tmp_path: Path, state: str
) -> None:
    """A stale kept value must never be written into the confs over a file that exists."""
    server, _ = _server(tmp_path, "wow-tbc")
    path = server / ".db_password"
    if state == "not utf-8":
        path.write_bytes(b"tbc-\xff\xfe\n")
    elif state == "empty":
        path.write_bytes(b"\n")
    else:
        path.unlink()
        path.mkdir()
    dbsecret.remember(
        TBC.id,
        composegen.install_id(server, platform_id=_linux),
        password="tbc-" + secrets.token_hex(8),
        volume="tbc-db-data",
        config_dir=platform.config_dir(),
    )
    texts, reasons = reset_defaults.default_texts(
        TBC, server, ["etc/mangosd.conf"], seams=_seams(_never, image_present=_never)
    )
    assert texts == {}
    assert ".db_password" in reasons["etc/mangosd.conf"]


def test_the_image_is_staged_beside_the_server_folder_and_cleared_after(tmp_path: Path) -> None:
    """Not the system temp dir: snap-packaged Docker has a private /tmp it writes into."""
    server, _ = _server(tmp_path, "wow-tbc")
    leftover = server / reset_defaults.RESET_STAGING
    leftover.mkdir()
    (leftover / "stale.conf.dist").write_text("x\n", encoding="utf-8")
    seen: list[Path] = []
    image = FakeImage(TEMPLATES["wow-tbc"])

    def copy(ref: str, src: str, dest: Path) -> None:
        seen.append(dest)
        assert not dest.exists(), "docker cp needs a dest that does not exist"
        image(ref, src, dest)

    texts, reasons = reset_defaults.default_texts(
        TBC, server, ["etc/realmd.conf"], seams=_seams(copy)
    )
    assert reasons == {} and set(texts) == {"etc/realmd.conf"}
    assert seen == [server / reset_defaults.RESET_STAGING]
    assert not (server / reset_defaults.RESET_STAGING).exists()
    assert sorted(p.name for p in server.iterdir()) == [".db_password"]


def test_a_file_outside_the_games_table_is_a_reason_not_a_key_error(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, "wow-tbc")
    texts, reasons = reset_defaults.default_texts(
        TBC,
        server,
        ["etc/realmd.conf", "etc/modules/extra.conf", OVERRIDE],
        seams=_seams(FakeImage(TEMPLATES["wow-tbc"])),
    )
    assert set(texts) == {"etc/realmd.conf"}
    assert set(reasons) == {"etc/modules/extra.conf", OVERRIDE}
    assert all(TBC.name in reason for reason in reasons.values())


def test_no_file_in_the_games_table_asks_no_docker(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, "wow-tbc")
    texts, reasons = reset_defaults.default_texts(
        TBC, server, ["etc/modules/extra.conf"], seams=_seams(_never, image_present=_never)
    )
    assert texts == {} and set(reasons) == {"etc/modules/extra.conf"}


# -- spec Tests 3-6 and 8: the operation ---------------------------------------


def _tuned_tbc(tmp_path: Path) -> tuple[Path, str, dict[str, bytes], dict[str, bytes]]:
    """A TBC install as the install wrote it, then tuned: (server, password, installed, tuned)."""
    server, password = _server(tmp_path, "wow-tbc")
    installed = _fresh_install("wow-tbc", server, FakeImage(TEMPLATES["wow-tbc"]))
    tuned: dict[str, bytes] = {}
    for file, data in installed.items():
        tuned[file] = data + b"Tuned.By.Hand = 7\r\n"
        (server / file).write_bytes(tuned[file])
    return server, password, installed, tuned


def _bytes(server: Path, files: object) -> dict[str, bytes]:
    return {file: (server / file).read_bytes() for file in files}  # type: ignore[attr-defined]


def _baks(server: Path) -> list[Path]:
    return sorted(server.rglob("*.bak"))


def test_a_reset_writes_the_install_default_and_backs_each_file_up_first(tmp_path: Path) -> None:
    server, _, installed, tuned = _tuned_tbc(tmp_path)
    report = reset_defaults.reset(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )
    assert [r.outcome for r in report.results] == ["reset"] * 4
    assert _bytes(server, installed) == installed
    for result in report.results:
        assert result.backup is not None and result.backup.read_bytes() == tuned[result.file]
        assert tuning.backups_of(server / result.file)[-1] == result.backup


def test_undo_brings_back_the_file_the_reset_replaced_byte_for_byte(tmp_path: Path) -> None:
    """Spec Test 4, through `reset_defaults.restore` (atomic; not `tuning.restore`)."""
    server, _, _, tuned = _tuned_tbc(tmp_path)
    report = reset_defaults.reset(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )
    undone = reset_defaults.undo(server, report.written)
    assert [r.outcome for r in undone.results] == ["restored"] * 4
    assert _bytes(server, tuned) == tuned


def test_one_file_that_cannot_be_built_means_no_file_is_written(tmp_path: Path) -> None:
    """Spec Test 3: build first, write second, all or nothing."""
    server, _, _, tuned = _tuned_tbc(tmp_path)
    templates = dict(TEMPLATES["wow-tbc"])
    del templates["ahbot.conf.dist"]
    report = reset_defaults.reset(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(templates))
    )
    assert report.refused and report.written == ()
    assert {r.file: r.outcome for r in report.results} == {
        "etc/mangosd.conf": "held",
        "etc/realmd.conf": "held",
        "etc/aiplayerbot.conf": "held",
        "etc/ahbot.conf": "refused",
    }
    assert _bytes(server, tuned) == tuned
    assert _baks(server) == []
    assert any("ahbot.conf.dist" in line for line in report.lines())


def test_a_write_that_fails_half_way_puts_back_what_it_had_already_written(
    tmp_path: Path,
) -> None:
    server, _, _, tuned = _tuned_tbc(tmp_path)
    written: list[Path] = []

    def second_write_fails(path: Path, text: str) -> None:
        if written:
            raise OSError(28, "No space left on device")
        written.append(path)
        conf.replace_file(path, text)

    report = reset_defaults.reset(
        TBC,
        server,
        reset_defaults.core_files(TBC),
        seams=_seams(FakeImage(TEMPLATES["wow-tbc"]), write=second_write_fails),
    )
    assert report.refused and report.written == ()
    assert _bytes(server, tuned) == tuned, "the first file was not put back"
    assert any("No space left" in line for line in report.lines())


def test_a_file_already_at_default_is_left_alone_with_no_backup(tmp_path: Path) -> None:
    """Spec Test 5."""
    server, _, installed, _ = _tuned_tbc(tmp_path)
    seams = _seams(FakeImage(TEMPLATES["wow-tbc"]))
    reset_defaults.reset(TBC, server, reset_defaults.core_files(TBC), seams=seams)
    baks = _baks(server)
    mtimes = {f: (server / f).stat().st_mtime_ns for f in installed}

    again = reset_defaults.reset(TBC, server, reset_defaults.core_files(TBC), seams=seams)

    assert [r.outcome for r in again.results] == ["already"] * 4
    assert _baks(server) == baks
    assert {f: (server / f).stat().st_mtime_ns for f in installed} == mtimes


def test_a_file_not_on_disk_is_reported_and_never_created(tmp_path: Path) -> None:
    """Spec correction 8: WotLK normally has no playerbots.conf, and must not get one."""
    _dist_install(tmp_path)
    (tmp_path / "env/dist/etc/modules/playerbots.conf").unlink()
    report = reset_defaults.reset(
        WOTLK, tmp_path, reset_defaults.AZEROTHCORE_CORE_FILES, seams=_seams()
    )
    assert [r.outcome for r in report.results] == ["reset", "reset", "absent"]
    assert not (tmp_path / "env/dist/etc/modules/playerbots.conf").exists()


def test_module_confs_and_scripts_are_never_touched(tmp_path: Path) -> None:
    """Spec Test 6: "All server settings" is the game's set and nothing else."""
    server, _, _, _ = _tuned_tbc(tmp_path)
    module_conf = server / "etc" / "modules" / "extra.conf"
    module_conf.parent.mkdir(parents=True, exist_ok=True)
    module_conf.write_bytes(b"Extra.Enable = 1\n")
    script = server / "lua" / "extra.lua"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(b"local x = 1\n")
    stamps = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (module_conf, script)}

    reset_defaults.reset(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )

    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in stamps} == stamps
    with pytest.raises(ValueError, match="extra.conf"):
        reset_defaults.reset(TBC, server, ["etc/modules/extra.conf"], seams=_seams())


def test_a_wotlk_reset_all_writes_the_override_too_and_backs_it_up(tmp_path: Path) -> None:
    """Owner decision 4, through the operation: backup first, so Undo works."""
    installed = _wotlk_stack(tmp_path)
    _dist_install(tmp_path)
    broken = b"services:\n  ac-worldserver:\n    environment:\n      TZ: Europe/Oslo\n"
    (tmp_path / OVERRIDE).write_bytes(broken)

    report = reset_defaults.reset(WOTLK, tmp_path, reset_defaults.core_files(WOTLK), seams=_seams())

    assert [r.outcome for r in report.results] == ["reset"] * 4
    assert (tmp_path / OVERRIDE).read_bytes() == installed
    override = next(r for r in report.results if r.file == OVERRIDE)
    assert override.backup is not None and override.backup.read_bytes() == broken
    reset_defaults.undo(tmp_path, report.written)
    assert (tmp_path / OVERRIDE).read_bytes() == broken


def test_the_override_of_a_stack_yulon_did_not_make_is_left_alone(tmp_path: Path) -> None:
    """Correction 20: an adopted server's compose files are another tool's."""
    _dist_install(tmp_path)
    (tmp_path / composegen.BASE_FILE).write_text("services: {}\n", encoding="utf-8")
    theirs = b"services:\n  theirs: {}\n"
    (tmp_path / OVERRIDE).write_bytes(theirs)

    report = reset_defaults.reset(WOTLK, tmp_path, reset_defaults.core_files(WOTLK), seams=_seams())

    assert [r.outcome for r in report.results] == ["reset", "reset", "reset", "foreign"]
    assert (tmp_path / OVERRIDE).read_bytes() == theirs
    assert not report.refused


def test_the_override_owes_a_recreate_and_a_conf_what_file_rule_says() -> None:
    """Correction 21: `file_rule` alone would call the override read-only, and raise no banner."""
    assert tuning.file_rule(OVERRIDE) == "read-only", "control: the gap this closes"
    assert reset_defaults.apply_rule(OVERRIDE) == "recreate"
    assert reset_defaults.apply_rule("env/dist/etc/worldserver.conf") == "restart"
    assert reset_defaults.apply_rule("etc/mangosd.conf") == tuning.file_rule("etc/mangosd.conf")


def test_no_password_reaches_a_report_or_a_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec Test 8: the texts carry the password; nothing the player or the log sees does."""
    server, password, _, _ = _tuned_tbc(tmp_path)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="yulon"):
        done = reset_defaults.reset(
            TBC,
            server,
            reset_defaults.core_files(TBC),
            seams=_seams(FakeImage(TEMPLATES["wow-tbc"])),
        )
        undone = reset_defaults.undo(server, done.written)

        def fails(path: Path, text: str) -> None:
            raise OSError(13, f"denied writing {len(text)} characters")

        refused = reset_defaults.reset(
            TBC,
            server,
            reset_defaults.core_files(TBC),
            seams=_seams(FakeImage(TEMPLATES["wow-tbc"]), write=fails),
        )
    for report in (done, undone, refused):
        assert password not in "\n".join(report.lines())
        assert password not in repr(report)
    assert password not in caplog.text
    assert "\n".join(refused.lines()), "control: the refusal said something"


def test_the_question_names_the_files_the_backup_the_undo_and_the_restart() -> None:
    one = reset_defaults.question(["env/dist/etc/worldserver.conf"], [])
    assert "worldserver.conf" in one and "backup" in one
    assert "Undo the last reset" in one and "restarted" in one
    assert "installed modules" not in one and "RECREATED" not in one
    many = reset_defaults.question(["env/dist/etc/worldserver.conf", OVERRIDE], ["NPC Beastmaster"])
    assert "worldserver.conf" in many and OVERRIDE in many
    assert "NPC Beastmaster" in many and "are kept as they are now" in many
    assert "RECREATED" in many and "by hand" in many


def test_a_file_the_rollback_could_not_put_back_stays_undoable_and_says_so(
    tmp_path: Path,
) -> None:
    """Its text is the default and its backup is the only record: Undo must still reach it."""
    server, _, _, tuned = _tuned_tbc(tmp_path)
    writes: list[Path] = []

    def second_write_fails(path: Path, text: str) -> None:
        if writes:
            raise OSError(28, "No space left on device")
        writes.append(path)
        conf.replace_file(path, text)

    def restore_fails(backup: Path, target: Path) -> None:
        raise OSError(13, "Permission denied")

    report = reset_defaults.reset(
        TBC,
        server,
        reset_defaults.core_files(TBC),
        seams=_seams(
            FakeImage(TEMPLATES["wow-tbc"]), write=second_write_fails, restore=restore_fails
        ),
    )
    assert report.refused
    assert [r.file for r in report.written] == ["etc/mangosd.conf"]
    text = "\n".join(report.lines())
    assert "mangosd.conf: NOT put back" in text and "NOT reset: it was reset" not in text
    assert "part-way" in report.lines()[0]
    reset_defaults.undo(server, report.written)
    assert _bytes(server, tuned) == tuned


def test_an_undo_that_fails_says_the_undo_failed_not_the_reset(tmp_path: Path) -> None:
    server, _, _, _ = _tuned_tbc(tmp_path)
    report = reset_defaults.reset(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )

    def restore_fails(backup: Path, target: Path) -> None:
        raise OSError(13, "Permission denied")

    undone = reset_defaults.undo(server, report.written, restore=restore_fails)
    assert undone.refused and undone.written == ()
    text = "\n".join(undone.lines())
    assert undone.lines()[0].startswith("The undo")
    assert "reset was not done" not in text and "NOT reset" not in text
    assert "mangosd.conf: NOT put back" in text and "Permission denied" in text


# -- owner decision 5: installed modules' keys in a core conf are kept ------------


def _tbc_rows(server: Path, installed: dict[str, frozenset[str]]) -> tuple[tuning.TuningRow, ...]:
    """The Tuning tab's own rows for this TBC server -- the source, never re-derived."""
    manifests = list(tbc_modules.store().load_all("mod"))
    return tuning.rows_for(manifests, installed, server)


XP_RATES = {"mod": frozenset({"xp-rates"})}


def test_carry_takes_the_live_line_byte_for_byte_and_keeps_the_default_lines_ending() -> None:
    default = "A = 1\r\nRate.XP.Kill = 1\r\nB = 2\r\n"
    live = 'Rate.XP.Kill   =  "3"\nA = 9\n'
    assert reset_defaults.carry_module_keys(default, live, ["Rate.XP.Kill"]) == (
        'A = 1\r\nRate.XP.Kill   =  "3"\r\nB = 2\r\n'
    )


def test_a_key_absent_or_only_commented_in_the_live_file_leaves_the_default() -> None:
    default = "Rate.XP.Kill = 1\n"
    assert (
        reset_defaults.carry_module_keys(default, "# Rate.XP.Kill = 9\n", ["Rate.XP.Kill"])
        == default
    )
    assert reset_defaults.carry_module_keys(default, "", ["Rate.XP.Kill"]) == default


def test_the_first_active_live_line_wins_and_a_key_the_default_lacks_is_appended() -> None:
    live = "Creatures.CustomIDs = 90001\nCreatures.CustomIDs = 1\n"
    assert reset_defaults.carry_module_keys("Key = 1\r\n", live, ["Creatures.CustomIDs"]) == (
        "Key = 1\r\nCreatures.CustomIDs = 90001\r\n"
    )


def test_module_keys_come_from_installed_modules_rows_only(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, "wow-tbc")
    files = reset_defaults.core_files(TBC)
    assert reset_defaults.module_keys(_tbc_rows(server, XP_RATES), files) == {
        "etc/mangosd.conf": ("Rate.XP.Kill", "Rate.XP.Quest", "Rate.XP.Explore")
    }
    assert reset_defaults.module_keys(_tbc_rows(server, {}), files) == {}, "not installed: no keys"


def _tuned_xp(tmp_path: Path) -> tuple[Path, str, dict[str, bytes]]:
    """A fresh TBC install whose XP rate (a module key) and world port (a core key) were changed."""
    server, password = _server(tmp_path, "wow-tbc")
    installed = _fresh_install("wow-tbc", server, FakeImage(TEMPLATES["wow-tbc"]))
    mangosd = installed["etc/mangosd.conf"].decode("utf-8")
    assert "Rate.XP.Kill = 1\n" in mangosd and "WorldServerPort = 8085\n" in mangosd  # control
    tuned = mangosd.replace("Rate.XP.Kill = 1\n", "Rate.XP.Kill    = 5\n").replace(
        "WorldServerPort = 8085\n", "WorldServerPort = 9999\n"
    )
    (server / "etc/mangosd.conf").write_bytes(tuned.encode("utf-8"))
    return server, password, installed


def test_an_installed_modules_key_survives_the_reset_while_a_core_key_goes_back(
    tmp_path: Path,
) -> None:
    server, _, _ = _tuned_xp(tmp_path)
    keys = reset_defaults.module_keys(_tbc_rows(server, XP_RATES), reset_defaults.core_files(TBC))
    report = reset_defaults.reset(
        TBC,
        server,
        reset_defaults.core_files(TBC),
        module_keys=keys,
        seams=_seams(FakeImage(TEMPLATES["wow-tbc"])),
    )
    text = (server / "etc/mangosd.conf").read_text(encoding="utf-8")
    assert "Rate.XP.Kill    = 5\n" in text, "the module's line was not kept byte for byte"
    assert "WorldServerPort = 8085\n" in text and "9999" not in text
    assert {r.file: r.outcome for r in report.results}["etc/mangosd.conf"] == "reset"


def test_a_module_that_is_not_installed_has_no_effect(tmp_path: Path) -> None:
    server, _, installed = _tuned_xp(tmp_path)
    keys = reset_defaults.module_keys(_tbc_rows(server, {}), reset_defaults.core_files(TBC))
    reset_defaults.reset(
        TBC,
        server,
        reset_defaults.core_files(TBC),
        module_keys=keys,
        seams=_seams(FakeImage(TEMPLATES["wow-tbc"])),
    )
    assert (server / "etc/mangosd.conf").read_bytes() == installed["etc/mangosd.conf"]


def test_a_live_file_that_is_not_utf8_is_refused_when_it_holds_module_keys(tmp_path: Path) -> None:
    server, _, _ = _tuned_xp(tmp_path)
    before = (server / "etc/mangosd.conf").read_bytes() + b"# caf\xe9\n"
    (server / "etc/mangosd.conf").write_bytes(before)
    report = reset_defaults.reset(
        TBC,
        server,
        reset_defaults.core_files(TBC),
        module_keys={"etc/mangosd.conf": ("Rate.XP.Kill",)},
        seams=_seams(FakeImage(TEMPLATES["wow-tbc"])),
    )
    assert report.refused and report.written == ()
    assert (server / "etc/mangosd.conf").read_bytes() == before
    assert _baks(server) == []


def test_carrying_module_keys_leaks_no_password(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec Test 8 again, down the decision-5 path: the carried text sits beside the logins."""
    server, password, _ = _tuned_xp(tmp_path)
    keys = reset_defaults.module_keys(_tbc_rows(server, XP_RATES), reset_defaults.core_files(TBC))
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="yulon"):
        report = reset_defaults.reset(
            TBC,
            server,
            reset_defaults.core_files(TBC),
            module_keys=keys,
            seams=_seams(FakeImage(TEMPLATES["wow-tbc"])),
        )
    assert f";{password};" in (server / "etc/mangosd.conf").read_text(encoding="utf-8")  # control
    assert password not in "\n".join(report.lines()) and password not in repr(report)
    assert password not in caplog.text


def test_the_apps_route_is_lazy_and_carries_the_module_keys_to_the_reset(tmp_path: Path) -> None:
    server, _, _ = _tuned_xp(tmp_path)
    image = FakeImage(TEMPLATES["wow-tbc"])
    route = reset_defaults.route_for_app(TBC, server, seams=_seams(image))
    assert image.copies == [], "binding the route built nothing"
    keys = reset_defaults.module_keys(_tbc_rows(server, XP_RATES), reset_defaults.core_files(TBC))
    report = route(reset_defaults.core_files(TBC), keys)
    assert len(image.copies) == 1 and not report.refused
    assert "Rate.XP.Kill    = 5\n" in (server / "etc/mangosd.conf").read_text(encoding="utf-8")


# -- fix round 1 ------------------------------------------------------------


def test_every_active_default_line_for_a_carried_key_takes_the_live_line() -> None:
    """Two active lines in the DEFAULT: both become the carried line, each keeping its ending."""
    default = "Rate.XP.Kill = 1\nA = 2\nRate.XP.Kill = 3\r\n"
    assert reset_defaults.carry_module_keys(default, "Rate.XP.Kill = 9\n", ["Rate.XP.Kill"]) == (
        "Rate.XP.Kill = 9\nA = 2\nRate.XP.Kill = 9\r\n"
    )


def test_a_key_the_install_table_writes_is_never_carried_even_if_a_module_declares_it(
    tmp_path: Path,
) -> None:
    """Lead ruling: the install's table WINS over decision 5's carry-over."""
    server, _, installed = _tuned_xp(tmp_path)  # WorldServerPort tuned to 9999
    table_keys = TBC.install.native.cmangos.conf.files["mangosd.conf"].keys  # type: ignore[union-attr]
    assert "WorldServerPort" in table_keys, "control: the install writes the port"
    report = reset_defaults.reset(
        TBC,
        server,
        reset_defaults.core_files(TBC),
        module_keys={"etc/mangosd.conf": ("Rate.XP.Kill", "worldserverport", "WorldServerPort")},
        seams=_seams(FakeImage(TEMPLATES["wow-tbc"])),
    )
    assert not report.refused
    text = (server / "etc/mangosd.conf").read_text(encoding="utf-8")
    assert "WorldServerPort = 8085\n" in text and "9999" not in text
    assert "Rate.XP.Kill    = 5\n" in text, "control: the module key itself was still carried"


def test_no_bundled_manifest_declares_a_key_an_install_table_writes() -> None:
    """Tripwire: today the ruling above changes nothing. A hit here is a report, not a fix.

    Per GAME: each game's manifests against that game's own table, file by
    file. One table keyed by file alone let a later game's table overwrite an
    earlier one's, so TBC's own keys were never compared (review, round 2).
    Spelled from the catalog, not through `install_keys()`, so a bug there
    cannot hide here.
    """
    games = ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise")
    tables: dict[tuple[str, str], frozenset[str]] = {}
    for game in games:
        native_block = CATALOG.get(game).install.native
        if native_block is None or native_block.cmangos is None:
            continue  # WotLK: its install writes no conf keys
        for name, patch in native_block.cmangos.conf.files.items():
            tables[(game, f"{ETC_DIR}/{name}")] = frozenset(k.casefold() for k in patch.keys)
    assert {game for game, _ in tables} == {"wow-tbc", "wow-vanilla", "wow-tortoise"}  # control
    overlaps: list[str] = []
    checked: dict[str, int] = dict.fromkeys(games, 0)
    for game in games:
        store = ManifestStore(resources.manifests_dir(), game)
        for kind in FAMILY_FILES:
            for manifest in store.load_all(kind):
                for block in manifest.conf:
                    written = tables.get((game, block.file))
                    if written is None:
                        continue
                    for key in block.keys:
                        checked[game] += 1
                        if key.key.casefold() in written:
                            overlaps.append(f"{game}/{manifest.id}: {block.file} {key.key}")
    for game in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        assert checked[game] > 0, f"control: some {game} manifest declares a key in a core conf"
    assert overlaps == []


def test_an_undo_whose_copy_dies_half_way_leaves_the_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead: `copy2` truncates, then writes; ENOSPC half-way would leave half a conf."""
    server, _, installed, _ = _tuned_tbc(tmp_path)
    report = reset_defaults.reset(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )
    before = {file: (server / file).stat().st_mode for file in installed}

    def half_then_full(src: object, dst: object, **kwargs: object) -> object:
        data = Path(str(src)).read_bytes()
        Path(str(dst)).write_bytes(data[: len(data) // 2])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copy2", half_then_full)
    undone = reset_defaults.undo(server, report.written)
    monkeypatch.undo()

    assert [r.outcome for r in undone.results] == ["refused"] * 4
    assert _bytes(server, installed) == installed, "a half-copied file replaced the reset one"
    assert sorted(p.name for p in (server / "etc").iterdir() if not p.name.endswith(".bak")) == [
        "ahbot.conf",
        "aiplayerbot.conf",
        "mangosd.conf",
        "realmd.conf",
    ], "a temp file was left beside the confs"
    again = reset_defaults.undo(server, report.written)
    assert [r.outcome for r in again.results] == ["restored"] * 4
    if sys.platform != "win32":
        assert {f: (server / f).stat().st_mode for f in installed} == before


def test_a_rollback_puts_a_file_back_through_the_atomic_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rollback path with the REAL `restore`, not an injected one: a rename, not a copy-over."""
    server, _, _, tuned = _tuned_tbc(tmp_path)
    modes = {file: (server / file).stat().st_mode for file in tuned}
    renames: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: object, dst: object) -> None:
        renames.append((Path(str(src)).name, Path(str(dst)).name))
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", spy)
    writes: list[Path] = []

    def second_write_fails(path: Path, text: str) -> None:
        if writes:
            raise OSError(28, "No space left on device")
        writes.append(path)
        conf.replace_file(path, text)

    report = reset_defaults.reset(
        TBC,
        server,
        reset_defaults.core_files(TBC),
        seams=_seams(
            FakeImage(TEMPLATES["wow-tbc"]),
            write=second_write_fails,
            restore=reset_defaults.restore,
        ),
    )
    monkeypatch.undo()

    assert {r.file: r.outcome for r in report.results}["etc/mangosd.conf"] == "held"
    assert report.refused and report.written == ()
    assert _bytes(server, tuned) == tuned
    assert ("mangosd.conf.yulon-restore-tmp", "mangosd.conf") in renames
    assert not list((server / "etc").glob("*.yulon-restore-tmp"))
    if sys.platform != "win32":
        assert {file: (server / file).stat().st_mode for file in tuned} == modes


# -- the undo after a restart: the last reset, read back off the disk -------------


def test_after_a_restart_the_last_reset_is_found_from_its_backups_on_disk(tmp_path: Path) -> None:
    """A crash or a restart loses the session's record; the backups beside each file are one."""
    server, _, _, tuned = _tuned_tbc(tmp_path)
    report = reset_defaults.reset(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )

    found = reset_defaults.last_reset_on_disk(TBC, server)

    assert found == report.written
    reset_defaults.undo(server, found)
    assert _bytes(server, tuned) == tuned
    assert reset_defaults.last_reset_on_disk(TBC, server) == (), "an undone reset is not undoable"


def test_the_disk_record_is_one_press_not_every_backup_ever_taken(tmp_path: Path) -> None:
    """An older backup of another file (another day's reset or save) is not the last press."""
    defaults = _dist_install(tmp_path)
    world, auth = (tmp_path / file for file in reset_defaults.AZEROTHCORE_CORE_FILES[:2])
    reset_defaults.reset_backup(auth, now=datetime(2026, 9, 1, 12, 0, 0))
    auth.write_bytes(b"Key = 3\n")
    made = reset_defaults.reset_backup(world, now=datetime(2026, 9, 23, 12, 0, 0))
    world.write_bytes(defaults[reset_defaults.AZEROTHCORE_CORE_FILES[0]])

    found = reset_defaults.last_reset_on_disk(WOTLK, tmp_path)

    assert found == (
        reset_defaults.FileResult(reset_defaults.AZEROTHCORE_CORE_FILES[0], "reset", made),
    )


def test_a_press_cut_short_is_found_as_far_as_it_got(tmp_path: Path) -> None:
    """A crash mid-reset: two files written, the third never reached, no rollback ran."""
    _dist_install(tmp_path)
    world, auth, bots = (tmp_path / file for file in reset_defaults.AZEROTHCORE_CORE_FILES)
    first = reset_defaults.reset_backup(world, now=datetime(2026, 9, 23, 12, 0, 0, 1000))
    world.write_bytes(b"Key = 1\n")
    second = reset_defaults.reset_backup(auth, now=datetime(2026, 9, 23, 12, 0, 0, 9000))
    auth.write_bytes(b"Key = 1\n")

    found = reset_defaults.last_reset_on_disk(WOTLK, tmp_path)

    assert [(r.file, r.backup) for r in found] == [
        (reset_defaults.AZEROTHCORE_CORE_FILES[0], first),
        (reset_defaults.AZEROTHCORE_CORE_FILES[1], second),
    ]
    assert bots.read_bytes() == b"Key = 2\n"


def test_a_backup_a_reset_did_not_take_is_not_a_reset(tmp_path: Path) -> None:
    """A Tuning save's backup, or someone's own `worldserver.conf.mine.bak`: never offered."""
    _dist_install(tmp_path)
    world = tmp_path / reset_defaults.AZEROTHCORE_CORE_FILES[0]
    world.with_name(world.name + ".mine.bak").write_bytes(b"Key = 9\n")
    world.with_name(world.name + ".20260923-120000-000000.reset.mine.bak").write_bytes(b"Key = 9\n")
    tuning.backup(world, now=datetime(2026, 9, 23, 13, 0, 0))
    world.write_bytes(b"Key = 5\n")
    assert reset_defaults.last_reset_on_disk(WOTLK, tmp_path) == ()
    made = reset_defaults.reset_backup(world, now=datetime(2026, 9, 23, 12, 0, 0))
    world.write_bytes(b"Key = 1\n")
    assert [r.backup for r in reset_defaults.last_reset_on_disk(WOTLK, tmp_path)] == [made]


def test_no_backups_is_nothing_to_undo(tmp_path: Path) -> None:
    _dist_install(tmp_path)
    assert reset_defaults.last_reset_on_disk(WOTLK, tmp_path) == ()
    assert reset_defaults.last_reset_on_disk(WOTLK, tmp_path / "gone") == ()


def test_a_reset_tags_its_backups_and_revert_still_finds_them_newest(tmp_path: Path) -> None:
    """The tag is what tells a reset's backup from a save's; the per-file Revert sees both."""
    _dist_install(tmp_path)
    world = tmp_path / reset_defaults.AZEROTHCORE_CORE_FILES[0]
    saved = tuning.backup(world, now=datetime(2026, 9, 23, 12, 0, 0))
    report = reset_defaults.reset(WOTLK, tmp_path, reset_defaults.AZEROTHCORE_CORE_FILES[:1])

    made = report.written[0].backup
    assert made is not None and made.name.endswith(".reset.bak")
    assert not saved.name.endswith(".reset.bak")
    assert tuning.backups_of(world) == (saved, made), "a name sort is still a time sort"


def test_a_save_after_the_reset_does_not_hide_the_reset_from_its_undo(tmp_path: Path) -> None:
    """Saving a module key into worldserver.conf after a reset backs it up too; Undo skips that."""
    _dist_install(tmp_path)
    report = reset_defaults.reset(WOTLK, tmp_path, reset_defaults.AZEROTHCORE_CORE_FILES)
    world = tmp_path / reset_defaults.AZEROTHCORE_CORE_FILES[0]
    tuning.write(world, {"Rate.XP.Kill": "3"})

    assert reset_defaults.last_reset_on_disk(WOTLK, tmp_path) == report.written


# -- fix round 1: an undo backs up first, and an undone reset stays undone ---------


def test_the_questions_last_word_is_the_job_the_banner_will_offer() -> None:
    """A CMaNGOS `etc/` file and the override owe a recreate: the question must not say restart."""
    for files in (["etc/mangosd.conf"], ["env/dist/etc/worldserver.conf", OVERRIDE]):
        said = reset_defaults.question(files, [])
        assert "until its containers are recreated" in said and "restarted" not in said, files
    said = reset_defaults.question(["env/dist/etc/authserver.conf"], [])
    assert "until it is restarted" in said and "recreated" not in said


def test_an_undo_backs_each_file_up_first_so_revert_brings_back_what_it_replaced(
    tmp_path: Path,
) -> None:
    server, _, installed, tuned = _tuned_tbc(tmp_path)
    report = reset_defaults.reset(
        TBC, server, reset_defaults.core_files(TBC), seams=_seams(FakeImage(TEMPLATES["wow-tbc"]))
    )
    undone = reset_defaults.undo(server, report.written)

    assert _bytes(server, tuned) == tuned
    for item in undone.results:
        newest = tuning.backups_of(server / item.file)[-1]
        assert item.before == newest and newest.name.endswith(".undo.bak")
        assert newest.read_bytes() == installed[item.file]
        assert newest.name in item.line()
        tuning.restore(newest, server / item.file)
    assert _bytes(server, installed) == installed, "Revert did not bring back what Undo replaced"


def test_an_undone_reset_edited_afterwards_is_not_offered_again(tmp_path: Path) -> None:
    """The review's case: Undo, then a Tuning save into the conf; the old reset must stay undone."""
    _dist_install(tmp_path)
    report = reset_defaults.reset(WOTLK, tmp_path, reset_defaults.AZEROTHCORE_CORE_FILES)
    reset_defaults.undo(tmp_path, report.written)
    tuning.write(tmp_path / reset_defaults.AZEROTHCORE_CORE_FILES[0], {"Rate.XP.Kill": "3"})

    assert reset_defaults.last_reset_on_disk(WOTLK, tmp_path) == ()
    assert reset_defaults.still_undoable(tmp_path, report.written) == ()


def test_reverting_the_undo_offers_the_undo_again(tmp_path: Path) -> None:
    """The file holds what the undo replaced again: the reset stands, so it is undoable again."""
    _dist_install(tmp_path)
    report = reset_defaults.reset(WOTLK, tmp_path, reset_defaults.AZEROTHCORE_CORE_FILES[:1])
    undone = reset_defaults.undo(tmp_path, report.written)
    before = undone.results[0].before
    assert before is not None
    tuning.restore(before, tmp_path / reset_defaults.AZEROTHCORE_CORE_FILES[0])

    assert reset_defaults.last_reset_on_disk(WOTLK, tmp_path) == report.written


def test_an_undo_that_cannot_back_up_first_leaves_the_file_and_stays_offered(
    tmp_path: Path,
) -> None:
    _dist_install(tmp_path)
    report = reset_defaults.reset(WOTLK, tmp_path, reset_defaults.AZEROTHCORE_CORE_FILES[:1])
    world = tmp_path / reset_defaults.AZEROTHCORE_CORE_FILES[0]
    reset_text = world.read_bytes()

    def backup_fails(path: Path) -> Path:
        raise OSError(28, "No space left on device")

    undone = reset_defaults.undo(tmp_path, report.written, backup=backup_fails)

    assert [r.outcome for r in undone.results] == ["refused"]
    assert "No space left on device" in "\n".join(undone.lines())
    assert world.read_bytes() == reset_text
    assert reset_defaults.last_reset_on_disk(WOTLK, tmp_path) == report.written


def test_an_undo_whose_copy_failed_stays_offered(tmp_path: Path) -> None:
    """Its backup was taken, then the copy failed: the file still holds the reset's text."""
    _dist_install(tmp_path)
    report = reset_defaults.reset(WOTLK, tmp_path, reset_defaults.AZEROTHCORE_CORE_FILES[:1])

    def restore_fails(backup: Path, target: Path) -> None:
        raise OSError(13, "Permission denied")

    undone = reset_defaults.undo(tmp_path, report.written, restore=restore_fails)

    assert undone.refused
    assert reset_defaults.last_reset_on_disk(WOTLK, tmp_path) == report.written
    assert reset_defaults.still_undoable(tmp_path, undone.results) == undone.results
