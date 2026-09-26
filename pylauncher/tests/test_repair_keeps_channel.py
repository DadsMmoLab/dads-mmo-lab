"""A Repair keeps a live command channel on, and its rollback still restores cleanly (T101).

A Repair is the install's own `run()` over a finished install, and every stage
runs again -- `generate-compose` included, which rewrites
`docker-compose.override.yml` whenever its text differs. It rendered the
install's plain override, without the channel's `enable_env`, so on yulon-ubuntu
(2026-09-24) the Repair wrote the pre-channel file back byte for byte, its own
`up` recreated the world without `AC_SOAP_ENABLED`, and the saved credential's
round trip found nothing listening. A rollback after it then read the file as
"edited since the press": it released the port and kept the `.before-channel`
backup, so the install still read as channel-on.

The same stage body is what Update to latest runs to put the compose back
(`_rewrite_what_we_own()`), and the T94 reset renders the same file; all three
ask `composegen.channel_world_env()` which environment the override carries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import channel_setup, reset_defaults, resources
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
OVERRIDE = composegen.OVERRIDE_FILE
CHANNEL_KEYS = ('AC_SOAP_ENABLED: "1"', 'AC_SOAP_IP: "0.0.0.0"', 'AC_RA_ENABLE: "0"')


def _linux() -> str:
    return "linux"


def _engine() -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        WOTLK,
        seams=native.Seams(
            platform_id=_linux, selinux_enforcing=lambda: False, fs_type=lambda path: "ext4"
        ),
    )


def _context(server_dir: Path) -> native.StageContext:
    return native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=WOTLK.id,
            install_id=composegen.install_id(server_dir, platform_id=_linux),
            family="azerothcore",
        ),
        cancel=None,
        secrets=native.Secrets(db_password=WOTLK.install.db_password(server_dir)),
    )


def _generate(server_dir: Path) -> str:
    """The `generate-compose` stage, as an install AND a Repair run it."""
    list(_engine().stage_generate_compose(_context(server_dir)))
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _press(server_dir: Path) -> str:
    channel_setup.enable(
        WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False
    )
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _tab(server_dir: Path) -> channel_setup.InstallChannel:
    """The Server tab's own setup object: its `roll_back()` is the button."""

    def never(*_args: object) -> object:
        raise AssertionError("a rollback creates no account and opens no channel")

    return channel_setup.InstallChannel(
        WOTLK,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(server_dir, platform_id=_linux),
        create=never,
        channel_for=never,
        db_password=WOTLK.install.db_password(server_dir),
    )


def _port_claim(server_dir: Path) -> str | None:
    return composegen.dotenv_value(server_dir, channel_setup.HOST_PORT_VAR)


def test_a_repair_keeps_a_live_channel_on(tmp_path: Path) -> None:
    installed = _generate(tmp_path)
    pressed = _press(tmp_path)
    assert pressed != installed, "control: the press changed the override"

    repaired = _generate(tmp_path)

    assert repaired == pressed
    for key in CHANNEL_KEYS:
        assert key in repaired


def test_a_rollback_after_a_repair_restores_the_installed_override(tmp_path: Path) -> None:
    installed = _generate(tmp_path)
    _press(tmp_path)
    _generate(tmp_path)

    assert _tab(tmp_path).roll_back()

    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8") == installed
    assert not (tmp_path / f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}").exists()
    assert _port_claim(tmp_path) == channel_setup.RELEASED_HOST_PORT
    assert not composegen.channel_is_on(tmp_path)


def test_a_repair_with_no_channel_writes_the_plain_override(tmp_path: Path) -> None:
    installed = _generate(tmp_path)
    assert _generate(tmp_path) == installed
    for key in CHANNEL_KEYS:
        assert key not in installed


def test_a_repair_after_a_rollback_leaves_the_channel_off(tmp_path: Path) -> None:
    installed = _generate(tmp_path)
    _press(tmp_path)
    assert _tab(tmp_path).roll_back()

    assert _generate(tmp_path) == installed


def test_a_rollback_that_left_the_file_alone_still_reads_as_off(tmp_path: Path) -> None:
    """The state the unfixed Repair left behind on yulon-ubuntu, measured 2026-09-24.

    The rollback released the port and KEPT the backup, because the file no
    longer said what the press wrote. The backup alone would read as a live
    press, and the next Repair would switch the channel's env back on for a
    user who pressed Roll back. The released port claim is the rollback's own
    record, and it is written by both of its arms.
    """
    installed = _generate(tmp_path)
    _press(tmp_path)
    (tmp_path / OVERRIDE).write_text(installed, encoding="utf-8")  # the unfixed Repair
    assert _tab(tmp_path).roll_back()
    assert (tmp_path / f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}").exists(), "control: kept"

    assert not composegen.channel_is_on(tmp_path)
    assert _generate(tmp_path) == installed
    assert reset_defaults.channel_is_on(tmp_path) is False


def test_the_repair_and_the_reset_render_the_same_channel_on_override(tmp_path: Path) -> None:
    """One helper for both regenerations, so the two can never disagree."""
    _generate(tmp_path)
    pressed = _press(tmp_path)
    texts, reasons = reset_defaults.default_texts(
        WOTLK,
        tmp_path,
        [OVERRIDE],
        seams=reset_defaults.Seams(
            image_present=lambda ref: False,
            copy_from_image=lambda *args: None,
            bind_label=lambda path: "",
            platform_id=_linux,
        ),
    )
    assert reasons == {}
    assert texts[OVERRIDE] == pressed == _generate(tmp_path)


@pytest.mark.parametrize(
    ("claim", "on"),
    [("127.0.0.1:7878", True), (None, True), ("127.0.0.1:0", False)],
    ids=["claimed", "no-claim-yet", "released"],
)
def test_the_channel_reads_as_on_from_its_backup_and_its_port_claim(
    tmp_path: Path, claim: str | None, on: bool
) -> None:
    assert not composegen.channel_is_on(tmp_path), "no backup: never on"
    (tmp_path / f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}").write_text("", encoding="utf-8")
    if claim is not None:
        composegen.write_dotenv(tmp_path, {channel_setup.HOST_PORT_VAR: claim})
    assert composegen.channel_is_on(tmp_path) is on


def test_the_last_assignment_in_dotenv_is_the_one_read(tmp_path: Path) -> None:
    """Compose takes the LAST assignment (`merge_dotenv()`), so this reads that one."""
    (tmp_path / composegen.DOTENV_FILE).write_text(
        "# comment\nDOCKER_SOAP_EXTERNAL_PORT=127.0.0.1:7878\n"
        "export DOCKER_SOAP_EXTERNAL_PORT = 127.0.0.1:0\nOTHER=x\n",
        encoding="utf-8",
    )
    assert composegen.dotenv_value(tmp_path, "DOCKER_SOAP_EXTERNAL_PORT") == "127.0.0.1:0"
    assert composegen.dotenv_value(tmp_path, "MISSING") is None
    assert composegen.dotenv_value(tmp_path / "nowhere", "OTHER") is None


def test_a_conf_channel_puts_nothing_into_the_override_env(tmp_path: Path) -> None:
    """TBC's channel is its mangosd.conf keys; its override carries no enable env."""
    (tmp_path / f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}").write_text("", encoding="utf-8")
    assert composegen.channel_is_on(tmp_path)
    assert composegen.channel_world_env(TBC, tmp_path) is None


def test_a_live_wotlk_channel_is_the_install_env_plus_its_enable(tmp_path: Path) -> None:
    assert composegen.channel_world_env(WOTLK, tmp_path) is None
    (tmp_path / f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}").write_text("", encoding="utf-8")
    operations = WOTLK.operations
    assert operations is not None
    assert composegen.channel_world_env(WOTLK, tmp_path) == {
        **composegen.world_env(WOTLK),
        **operations.enable_env,
    }
