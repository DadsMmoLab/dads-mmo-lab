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

import secrets
import sys
from pathlib import Path

import pytest

from yulon import channel_setup, reset_defaults, resources
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import conf
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError

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
