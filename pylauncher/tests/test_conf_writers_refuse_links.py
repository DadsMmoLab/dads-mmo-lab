"""T573: the conf writers that back a file up first refuse a link that leads out of the install.

`tuning.backup(path)` with no root trusts the file's own folder; each of these writers passes
`root=server_dir`, so a linked PARENT folder (`etc` -> somewhere else) is seen. A test that
only plants a linked FILE cannot tell the two apart (the file itself is checked either way),
which is why these plant the folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.test_bot_population import CONF as CMANGOS_CONF
from tests.test_bot_population import TBC, _cmangos_conf
from tests.test_rebuild_random_bots import CONF_TEXT, TOKEN, TORTOISE, _conf
from tests.test_server_time_zone import OSLO, WOTLK, _installed
from yulon import bot_population, server_time_zone
from yulon.controller_wow_tortoise import poolreset

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")


def _link_folder_out(server: Path, folder: str, tmp_path: Path) -> Path:
    """Move `server/folder` outside the server and leave a link to it in its place."""
    outside = tmp_path / "somebody-elses-folder"
    (server / folder).rename(outside)
    (server / folder).symlink_to(outside, target_is_directory=True)
    return outside


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_the_bot_count_will_not_back_up_or_write_through_a_linked_conf_folder(
    tmp_path: Path,
) -> None:
    """Mutation: `tuning.backup(path)` without `root=` in `bot_population.write`."""
    server = tmp_path / "server"
    server.mkdir()
    _cmangos_conf(server)
    outside = _link_folder_out(server, Path(CMANGOS_CONF).parent.as_posix(), tmp_path)
    before = _snapshot(outside)

    with pytest.raises(bot_population.BotCountError, match="outside the server folder"):
        bot_population.write(TBC, server, 50)

    assert _snapshot(outside) == before


def test_a_pool_reset_request_will_not_back_up_or_write_through_a_linked_conf_folder(
    tmp_path: Path,
) -> None:
    """Mutation: `tuning.backup(path)` without `root=` in `poolreset._write_value`."""
    server = tmp_path / "server"
    server.mkdir()
    _conf(server)
    outside = _link_folder_out(server, Path(bot_population.CONF_FILE).parent.as_posix(), tmp_path)
    before = _snapshot(outside)

    with pytest.raises(poolreset.PoolResetError, match="outside the server folder"):
        poolreset.write_key(TORTOISE, server, TOKEN)

    assert _snapshot(outside) == before
    assert CONF_TEXT.encode("utf-8") in before.values()


def test_a_time_zone_will_not_back_up_or_write_through_a_linked_override(
    tmp_path: Path,
) -> None:
    """The override sits in the server folder itself, so only the FILE can be a link.

    (Dropping `root=` there is an equivalent change: the file's own folder IS the root.)
    Mutation: drop the `tuning.backup` call and the outside file is rewritten.
    """
    server = tmp_path / "server"
    server.mkdir()
    _installed(server, WOTLK)
    override = server / server_time_zone.FILE
    outside = tmp_path / "somebody-elses-override.yml"
    outside.write_bytes(override.read_bytes())
    override.unlink()
    override.symlink_to(outside)
    before = outside.read_bytes()

    with pytest.raises(server_time_zone.TimeZoneSettingError, match="outside the server folder"):
        server_time_zone.write(WOTLK, server, OSLO)

    assert outside.read_bytes() == before
