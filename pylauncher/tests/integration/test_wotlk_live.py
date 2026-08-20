"""Phase 1 exit criteria against the real AzerothCore fixture (`tests/fixture.md`).

Doubly opt-in: a Docker daemon must be reachable *and* `YULON_WOTLK_SERVER_DIR`
must point at a compose project whose images are already built/pulled (the
fixture's first boot builds the worldserver from source and can take hours —
that is install work, not a test's job). The test refuses to touch an install
that is already running: it needs a genuine start → ready → stop cycle, and
stopping someone's live server from a test would be the wrong kind of surprise.

    YULON_WOTLK_SERVER_DIR=~/wow-server-playerbots pytest -m integration tests/integration

Optional: `YULON_WOTLK_REALM_ADDRESS` (default `127.0.0.1`) — the address the
authserver logs for the realm, i.e. `dml-start.sh`'s `DML_REALM_ADDRESS`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yulon.controller_wow_wotlk.controller import WotlkController

pytestmark = pytest.mark.integration

_SERVER_DIR_ENV = "YULON_WOTLK_SERVER_DIR"
_REALM_ADDRESS_ENV = "YULON_WOTLK_REALM_ADDRESS"
_REALM_PORT = 8085

# Mirror `dml-start.sh`'s budgets (~90 × 2s for the DB, ~240 × 2s for ready).
_DB_TIMEOUT = 180.0
_READY_TIMEOUT = 480.0
_INTERVAL = 2.0


@pytest.fixture
def wotlk_server_dir(require_docker: None) -> Path:
    """The fixture's compose dir from the environment, or skip."""
    raw = os.environ.get(_SERVER_DIR_ENV)
    if not raw:
        pytest.skip(f"{_SERVER_DIR_ENV} not set; skipping the AzerothCore fixture run")
    server_dir = Path(raw).expanduser()
    if not (server_dir / "docker-compose.yml").is_file():
        pytest.skip(f"{_SERVER_DIR_ENV}={server_dir} has no docker-compose.yml")
    return server_dir


def test_wotlk_controller_start_ready_stop(wotlk_server_dir: Path) -> None:
    """`WotlkController` brings the fixture up to 'ready...' and back down."""
    ctl = WotlkController(wotlk_server_dir)
    if ctl.status().any_running:
        pytest.skip("the WotLK install is already running; stop it before running this test")
    if ctl.port_conflicts():
        pytest.skip(f"another container holds {ctl.spec.ports}; stop it first")

    realm_address = os.environ.get(_REALM_ADDRESS_ENV, "127.0.0.1")
    ctl.start()
    try:
        assert ctl.wait_db_healthy(timeout=_DB_TIMEOUT, interval=_INTERVAL) is True
        assert (
            ctl.wait_ready(realm_address, _REALM_PORT, timeout=_READY_TIMEOUT, interval=_INTERVAL)
            is True
        )
        assert ctl.status().all_running is True
    finally:
        ctl.stop()
    assert ctl.status().any_running is False
