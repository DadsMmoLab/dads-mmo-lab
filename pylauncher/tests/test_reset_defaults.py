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

from yulon import reset_defaults
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import conf
from yulon.catalog.families.cmangos import CmangosInstaller

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
