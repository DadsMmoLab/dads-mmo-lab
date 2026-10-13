"""A fresh WotLK or Unbound install starts with its command channel on (T723).

The owner decided it on 2026-10-12: the way the CMaNGOS conf trees have since
T87, the install writes the channel and the first boot binds it, so the account
is proved without a press. Until this, an environment-route channel came on only
from "Turn on the command channel", so a fresh install booted with
`AC_SOAP_ENABLED` absent, and its post-install settle asked a port nothing was
listening on.

Only the press that made the folder may switch the channel on: the Install button
and the install harness set `first_install`, and nothing else does. The disk
cannot tell a first install from a folder whose override was deleted later, so
the tests below also cover what an Update and a move do to such a folder.

Every test drives `generate-compose`, the stage the install runs for its compose
files, through the same engine the install uses. Update is driven through
`_rewrite_what_we_own()`, the route Update takes. Nothing here calls the press to
get the channel on.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from tests.support_native import Recorder
from tests.support_native import engine as pipeline_engine
from tests.support_native import install as run_install_pipeline
from tests.test_families_cmangos import context as cm_context
from tests.test_families_cmangos import engine as cm_engine
from yulon import channel_setup, resources
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
UNBOUND = CATALOG.get("wow-unbound")
OVERRIDE = composegen.OVERRIDE_FILE
BACKUP = f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}"
CHANNEL_KEYS = ('AC_SOAP_ENABLED: "1"', 'AC_SOAP_IP: "0.0.0.0"', 'AC_RA_ENABLE: "0"')


def _linux() -> str:
    return "linux"


def _engine(entry) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        entry,
        seams=native.Seams(
            platform_id=_linux,
            selinux_enforcing=lambda: False,
            fs_type=lambda path: "ext4",
        ),
    )


def _context(entry, server_dir: Path, *, first_install: bool) -> native.StageContext:
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
        first_install=first_install,
    )


def _generate(entry, server_dir: Path, *, first_install: bool) -> str:
    """The `generate-compose` stage, run by the install's press (True) or a later one (False)."""
    engine = _engine(entry)
    context = _context(entry, server_dir, first_install=first_install)
    list(engine.stage_generate_compose(context))
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _update(entry, server_dir: Path) -> str:
    """Update to latest's put-back, through `_rewrite_what_we_own()`, which runs the stage."""
    state = native.InstallState(
        game_id=entry.id,
        install_id=composegen.install_id(server_dir, platform_id=_linux),
        family="azerothcore",
    )
    options = InstallOptions(server_dir=server_dir)
    list(_engine(entry)._rewrite_what_we_own(server_dir, options, state))
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


def _installed_off(entry, server_dir: Path) -> str:
    """A finished install whose channel is off, made the way a person makes it:
    the install, then the rollback.

    The rollback leaves the released claim `127.0.0.1:0` in `.env`.
    """
    _generate(entry, server_dir, first_install=True)
    assert _tab(entry, server_dir).roll_back() is True
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _installed_no_port_line(entry, server_dir: Path) -> str:
    """An install the way it stood before T723: channel off, no backup, and no port line in `.env`.

    Made by the stage with no first-install flag, on an empty folder, which is what
    a pre-T723 install's own stage wrote. Nothing was ever pressed, so `.env` never
    got a `DOCKER_SOAP_EXTERNAL_PORT` line at all.
    """
    override = _generate(entry, server_dir, first_install=False)
    assert _claim(server_dir) is None, "control: no port line in .env"
    assert not (server_dir / BACKUP).exists(), "control: no press record"
    return override


def _claim(server_dir: Path) -> str | None:
    return composegen.dotenv_value(server_dir, channel_setup.HOST_PORT_VAR)


def test_a_fresh_wotlk_install_starts_with_the_channel_on(tmp_path: Path) -> None:
    override = _generate(WOTLK, tmp_path, first_install=True)

    for key in CHANNEL_KEYS:
        assert key in override
    assert composegen.channel_is_on(tmp_path)


def test_the_channel_port_on_a_fresh_install_is_claimed_on_loopback_only(tmp_path: Path) -> None:
    _generate(WOTLK, tmp_path, first_install=True)

    assert _claim(tmp_path) == "127.0.0.1:7878"


def test_a_fresh_unbound_install_claims_its_own_loopback_port(tmp_path: Path) -> None:
    override = _generate(UNBOUND, tmp_path, first_install=True)

    for key in CHANNEL_KEYS:
        assert key in override
    assert _claim(tmp_path) == "127.0.0.1:7879"


def test_a_repair_of_a_fresh_install_keeps_the_channel_on(tmp_path: Path) -> None:
    first = _generate(WOTLK, tmp_path, first_install=True)
    again = _generate(WOTLK, tmp_path, first_install=False)

    assert again == first
    for key in CHANNEL_KEYS:
        assert key in again


def test_the_server_tabs_rollback_turns_a_fresh_channel_off(tmp_path: Path) -> None:
    _generate(WOTLK, tmp_path, first_install=True)

    assert _tab(WOTLK, tmp_path).roll_back() is True

    override = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert "AC_SOAP_ENABLED" not in override
    assert not (tmp_path / BACKUP).exists()
    assert _claim(tmp_path) == channel_setup.RELEASED_HOST_PORT
    assert not composegen.channel_is_on(tmp_path)


@pytest.mark.parametrize("first_install", [False, True], ids=["update", "first-install-flag"])
def test_a_turned_off_install_is_not_switched_back_on_by_a_later_run(
    tmp_path: Path, first_install: bool
) -> None:
    """With the override still there, no later run may switch the channel back on."""
    _generate(WOTLK, tmp_path, first_install=True)
    _press(WOTLK, tmp_path)
    assert _tab(WOTLK, tmp_path).roll_back() is True
    off = (tmp_path / OVERRIDE).read_text(encoding="utf-8")

    again = _generate(WOTLK, tmp_path, first_install=first_install)

    assert again == off
    assert "AC_SOAP_ENABLED" not in again
    assert not composegen.channel_is_on(tmp_path)


def test_a_rolled_back_install_whose_override_is_gone_stays_off_on_update(tmp_path: Path) -> None:
    """A rollback leaves `127.0.0.1:0` in `.env`.

    Losing the override must not make an Update a first install.
    """
    _installed_off(WOTLK, tmp_path)
    (tmp_path / OVERRIDE).unlink()

    override = _update(WOTLK, tmp_path)

    assert "AC_SOAP_ENABLED" not in override
    assert _claim(tmp_path) == channel_setup.RELEASED_HOST_PORT
    assert not (tmp_path / BACKUP).exists()
    assert not composegen.channel_is_on(tmp_path)


def test_a_never_pressed_install_whose_override_is_gone_stays_off_on_update(tmp_path: Path) -> None:
    """A pre-T723 install that was never pressed has no port line.

    Losing its override must not turn SOAP on.
    """
    _installed_no_port_line(WOTLK, tmp_path)
    (tmp_path / OVERRIDE).unlink()

    override = _update(WOTLK, tmp_path)

    assert "AC_SOAP_ENABLED" not in override
    assert _claim(tmp_path) is None
    assert not (tmp_path / BACKUP).exists()


def test_a_first_install_through_the_install_pipeline_starts_on(tmp_path: Path) -> None:
    """The real `run()`, with the stages the harness stubs: the Install button's path."""
    server_dir = tmp_path / "wow"

    run_install_pipeline(Recorder(images=False), server_dir)

    override = (server_dir / OVERRIDE).read_text(encoding="utf-8")
    for key in CHANNEL_KEYS:
        assert key in override
    assert _claim(server_dir) == "127.0.0.1:7878"


def test_an_install_run_no_install_button_started_leaves_the_channel_off(tmp_path: Path) -> None:
    """`run()` is also the move's engine, with no first-install flag.

    The move must not switch SOAP on.
    """
    server_dir = tmp_path / "wow"

    list(pipeline_engine(Recorder(images=False)).run(InstallOptions(server_dir=server_dir)))

    assert "AC_SOAP_ENABLED" not in (server_dir / OVERRIDE).read_text(encoding="utf-8")
    assert _claim(server_dir) is None
    assert not (server_dir / BACKUP).exists()


def test_a_first_install_into_a_folder_that_already_claims_the_port_stays_off(
    tmp_path: Path,
) -> None:
    """The second guard: a port line in `.env` says a channel was here before."""
    released = channel_setup.RELEASED_HOST_PORT
    composegen.write_dotenv(tmp_path, {channel_setup.HOST_PORT_VAR: released})

    override = _generate(WOTLK, tmp_path, first_install=True)

    assert "AC_SOAP_ENABLED" not in override
    assert _claim(tmp_path) == channel_setup.RELEASED_HOST_PORT
    assert not composegen.channel_is_on(tmp_path)


def test_a_reinstall_into_a_folder_with_an_override_is_an_install_and_switches_on(
    tmp_path: Path,
) -> None:
    """The Install button over an existing folder is an install run, so the channel comes on.

    Nothing in the folder says otherwise: no port line in `.env`.
    """
    _installed_no_port_line(WOTLK, tmp_path)

    override = _generate(WOTLK, tmp_path, first_install=True)

    for key in CHANNEL_KEYS:
        assert key in override
    assert _claim(tmp_path) == "127.0.0.1:7878"
    assert composegen.channel_is_on(tmp_path)


def test_a_reinstall_keeps_the_press_record_it_finds(tmp_path: Path) -> None:
    """A record of the override before any channel is the first kept, never replaced."""
    before = _installed_no_port_line(WOTLK, tmp_path)
    leftover = "# the record of an earlier press\n"
    (tmp_path / BACKUP).write_text(leftover, encoding="utf-8")

    _generate(WOTLK, tmp_path, first_install=True)

    assert (tmp_path / BACKUP).read_text(encoding="utf-8") == leftover
    assert leftover != before
    assert _claim(tmp_path) == "127.0.0.1:7878"


def test_an_env_with_the_users_own_port_line_is_left_alone(tmp_path: Path) -> None:
    """The user set DOCKER_SOAP_EXTERNAL_PORT: no SOAP setting is written, and it is said."""
    mine = f"{channel_setup.HOST_PORT_VAR}=0.0.0.0:9999"
    (tmp_path / composegen.DOTENV_FILE).write_text(f"{mine}\nOTHER=1\n", encoding="utf-8")
    env_before = (tmp_path / composegen.DOTENV_FILE).read_bytes()

    engine = _engine(WOTLK)
    said = list(engine.stage_generate_compose(_context(WOTLK, tmp_path, first_install=True)))

    override = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert "AC_SOAP_ENABLED" not in override
    assert (tmp_path / composegen.DOTENV_FILE).read_bytes() == env_before
    assert not (tmp_path / BACKUP).exists()
    assert not composegen.channel_is_on(tmp_path)
    refusal = next(line for line in said if channel_setup.HOST_PORT_VAR in line)
    assert "left as it is" in refusal
    assert "already set" in refusal
    assert "chosen" not in refusal, "Yu'lon itself may have written the line, not the player"


def test_a_claim_written_changes_only_one_env_line(tmp_path: Path) -> None:
    """The user's other .env lines and comments stay byte for byte.

    What follows them is `write_dotenv()`'s one appended block (the shared writer the press
    uses too): a blank line, its comment, and the single port line.
    """
    original = "# my notes\nDOCKER_DB_EXTERNAL_PORT=3307\n\n# keep this\nOTHER=a b  \n"
    (tmp_path / composegen.DOTENV_FILE).write_bytes(original.encode("utf-8"))

    _generate(WOTLK, tmp_path, first_install=True)

    after = (tmp_path / composegen.DOTENV_FILE).read_text(encoding="utf-8")
    port = f"{channel_setup.HOST_PORT_VAR}=127.0.0.1:7878"
    assert after == original + "\n# Written by Yu'lon's install engine.\n" + port + "\n"
    assert after.count(channel_setup.HOST_PORT_VAR) == 1


@pytest.mark.parametrize("first_install", [False, True], ids=["update", "install"])
def test_an_oserror_while_rendering_is_not_called_a_channel_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_install: bool
) -> None:
    """Only the channel's own write is worded as the channel's; a render fault is as before."""
    engine = _engine(WOTLK)

    def unreadable(*_args: object) -> object:
        raise PermissionError("the override cannot be read")

    monkeypatch.setattr(engine, "_render_compose", unreadable)

    with pytest.raises(OSError, match="the override cannot be read") as caught:
        list(engine.stage_generate_compose(_context(WOTLK, tmp_path, first_install=first_install)))
    assert not isinstance(caught.value, InstallerError)


def test_a_failed_channel_write_is_worded_as_the_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*_args: object) -> None:
        raise PermissionError("no write")

    monkeypatch.setattr(composegen, "record_channel_press", refused)

    with pytest.raises(InstallerError, match="command channel could not be written"):
        list(_engine(WOTLK).stage_generate_compose(_context(WOTLK, tmp_path, first_install=True)))


def test_a_non_install_path_with_every_file_missing_does_not_switch_on(tmp_path: Path) -> None:
    """Missing files are not a first install: Update into an empty folder leaves SOAP off."""
    override = _update(WOTLK, tmp_path)

    assert "AC_SOAP_ENABLED" not in override
    assert _claim(tmp_path) is None
    assert not (tmp_path / BACKUP).exists()
    assert not composegen.channel_is_on(tmp_path)


def test_the_stage_without_the_flag_on_an_empty_folder_does_not_switch_on(tmp_path: Path) -> None:
    override = _generate(WOTLK, tmp_path, first_install=False)

    assert "AC_SOAP_ENABLED" not in override
    assert _claim(tmp_path) is None


def test_a_cmangos_tree_is_not_switched_on_by_the_compose_stage(tmp_path: Path) -> None:
    """TBC's channel is its conf file's, written at install by T87.

    Nothing else switches it on, even on a first install.
    """
    ctx = dataclasses.replace(cm_context(tmp_path), first_install=True)

    list(cm_engine(Recorder()).stage_generate_compose(ctx))

    assert not (tmp_path / BACKUP).exists()
    assert _claim(tmp_path) is None


def test_the_env_file_stays_owner_only_once_the_claim_is_written(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    _generate(WOTLK, tmp_path, first_install=True)

    env = tmp_path / composegen.DOTENV_FILE
    assert env.is_file()
    assert env.stat().st_mode & 0o777 == 0o600


def test_a_leftover_press_record_is_never_overwritten_by_the_switch_on(tmp_path: Path) -> None:
    """A backup from a press that outlived its override is kept as it is, never replaced.

    With no port line in `.env` the folder gives no reason to refuse, so the channel is on.
    """
    leftover = "# the record of an earlier press\n"
    (tmp_path / BACKUP).write_text(leftover, encoding="utf-8")

    _generate(WOTLK, tmp_path, first_install=True)

    assert (tmp_path / BACKUP).read_text(encoding="utf-8") == leftover
