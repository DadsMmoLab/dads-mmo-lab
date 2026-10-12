"""A fresh WotLK or Unbound install starts with its command channel on (T723).

The owner decided it on 2026-10-12: the way the CMaNGOS conf trees have since
T87, the install writes the channel and the first boot binds it, so the account
is proved without a press. Until this, an environment-route channel came on only
from "Turn on the command channel", so a fresh install booted with
`AC_SOAP_ENABLED` absent, and its post-install settle asked a port nothing was
listening on.

Every test drives `generate-compose`, the stage the install runs for its compose
files and a Repair runs again, through the same engine the install uses. Nothing
here calls the press to get the channel on.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.support_native import Recorder
from tests.test_families_cmangos import context as cm_context
from tests.test_families_cmangos import engine as cm_engine
from yulon import channel_setup, resources
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
UNBOUND = CATALOG.get("wow-unbound")
OVERRIDE = composegen.OVERRIDE_FILE
BACKUP = f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}"
CHANNEL_KEYS = ('AC_SOAP_ENABLED: "1"', 'AC_SOAP_IP: "0.0.0.0"', 'AC_RA_ENABLE: "0"')


def _linux() -> str:
    return "linux"


def _generate(entry, server_dir: Path) -> str:
    """The `generate-compose` stage, as the install and a Repair both run it."""
    engine = AzerothCoreInstaller(
        entry,
        seams=native.Seams(
            platform_id=_linux,
            selinux_enforcing=lambda: False,
            fs_type=lambda path: "ext4",
        ),
    )
    context = native.StageContext(
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
    list(engine.stage_generate_compose(context))
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _tab(entry, server_dir: Path) -> channel_setup.InstallChannel:
    """The Server tab's setup object, whose `roll_back()` is the button."""

    def never(*_args: object) -> object:
        raise AssertionError("a rollback creates no account and opens no channel")

    return channel_setup.InstallChannel(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(server_dir, platform_id=_linux),
        create=never,
        channel_for=never,
        db_password=entry.install.db_password(server_dir),
    )


def _press(entry, server_dir: Path) -> None:
    """The Turn on press, for an install that was switched off."""
    channel_setup.enable(
        entry, server_dir, templates_root=resources.installers_dir(), world_running=False
    )


def _claim(server_dir: Path) -> str | None:
    return composegen.dotenv_value(server_dir, channel_setup.HOST_PORT_VAR)


def test_a_fresh_wotlk_install_starts_with_the_channel_on(tmp_path: Path) -> None:
    override = _generate(WOTLK, tmp_path)

    for key in CHANNEL_KEYS:
        assert key in override
    assert composegen.channel_is_on(tmp_path)


def test_the_channel_port_on_a_fresh_install_is_claimed_on_loopback_only(tmp_path: Path) -> None:
    _generate(WOTLK, tmp_path)

    assert _claim(tmp_path) == "127.0.0.1:7878"


def test_a_fresh_unbound_install_claims_its_own_loopback_port(tmp_path: Path) -> None:
    override = _generate(UNBOUND, tmp_path)

    for key in CHANNEL_KEYS:
        assert key in override
    assert _claim(tmp_path) == "127.0.0.1:7879"


def test_a_repair_of_a_fresh_install_keeps_the_channel_on(tmp_path: Path) -> None:
    first = _generate(WOTLK, tmp_path)
    again = _generate(WOTLK, tmp_path)

    assert again == first
    for key in CHANNEL_KEYS:
        assert key in again


def test_the_server_tabs_rollback_turns_a_fresh_channel_off(tmp_path: Path) -> None:
    _generate(WOTLK, tmp_path)

    assert _tab(WOTLK, tmp_path).roll_back() is True

    override = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert "AC_SOAP_ENABLED" not in override
    assert not (tmp_path / BACKUP).exists()
    assert _claim(tmp_path) == channel_setup.RELEASED_HOST_PORT
    assert not composegen.channel_is_on(tmp_path)


def test_a_turned_off_install_is_not_switched_back_on_by_a_repair(tmp_path: Path) -> None:
    """The switch-on is for a folder with no override yet, never for one a rollback took back."""
    _generate(WOTLK, tmp_path)
    _press(WOTLK, tmp_path)
    assert _tab(WOTLK, tmp_path).roll_back() is True
    off = (tmp_path / OVERRIDE).read_text(encoding="utf-8")

    again = _generate(WOTLK, tmp_path)

    assert again == off
    assert "AC_SOAP_ENABLED" not in again
    assert not composegen.channel_is_on(tmp_path)


def test_a_cmangos_tree_is_not_switched_on_by_the_compose_stage(tmp_path: Path) -> None:
    """TBC's channel is its conf file's, and T87 already writes it at install. Nothing else."""
    list(cm_engine(Recorder()).stage_generate_compose(cm_context(tmp_path)))

    assert not (tmp_path / BACKUP).exists()
    assert _claim(tmp_path) is None


def test_the_env_file_stays_owner_only_once_the_claim_is_written(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    _generate(WOTLK, tmp_path)

    env = tmp_path / composegen.DOTENV_FILE
    assert env.is_file()
    assert env.stat().st_mode & 0o777 == 0o600


def test_a_leftover_press_record_is_never_overwritten_by_the_switch_on(tmp_path: Path) -> None:
    """A backup from a press that outlived its override is kept as it is, never replaced."""
    leftover = "# the record of an earlier press\n"
    (tmp_path / BACKUP).write_text(leftover, encoding="utf-8")

    _generate(WOTLK, tmp_path)

    assert (tmp_path / BACKUP).read_text(encoding="utf-8") == leftover
