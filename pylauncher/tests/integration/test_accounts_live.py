"""Account creation against a real AzerothCore database, and against the real server.

Three layers of opt-in, all self-skipping, so the default `pytest` run never
touches a database:

- a Docker daemon must answer (`require_docker`);
- the WotLK database container must be **running** — these tests write to
  `acore_auth`, so they refuse to guess at a stopped or absent one;
- the byte-exactness test additionally needs a *ready worldserver* and a pty,
  because the whole point of it is that the SERVER writes the verifier and we
  only check it.

    YULON_WOTLK_DB_CONTAINER=ac-database pytest -m integration tests/integration

Optional: `YULON_WOTLK_DB_PASSWORD` (default `password`, the acore-docker
default), `YULON_WOTLK_WORLD_CONTAINER` (default `ac-worldserver`).

Every account these tests make starts with `YULONIT` and is deleted in a
`finally`, including on failure.
"""

from __future__ import annotations

import os
import secrets

import pytest

from yulon import runner
from yulon.apply import DockerSql
from yulon.controller_wow_wotlk import accounts, console, docker_ctl

pytestmark = pytest.mark.integration

_DB_CONTAINER_ENV = "YULON_WOTLK_DB_CONTAINER"
_DB_PASSWORD_ENV = "YULON_WOTLK_DB_PASSWORD"
_WORLD_CONTAINER_ENV = "YULON_WOTLK_WORLD_CONTAINER"

# Short enough to leave room for a random suffix under MAX_ACCOUNT_STR (17).
_PREFIX = "YULONIT"
# Not a real user's password, but shaped like one, and within MAX_PASS_STR.
_PASSWORD = "Passw0rd!Mix"


def _running(container: str) -> bool:
    try:
        proc = runner.run(["docker", "inspect", "-f", "{{.State.Running}}", container])
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


@pytest.fixture
def sql(require_docker: None) -> DockerSql:
    """A `DockerSql` on the running WotLK database container, or skip."""
    container = os.environ.get(_DB_CONTAINER_ENV, docker_ctl.SPEC.db)
    if not _running(container):
        pytest.skip(f"the WotLK database container {container!r} is not running")
    return DockerSql(container, os.environ.get(_DB_PASSWORD_ENV, "password"))


def _throwaway_name() -> str:
    return f"{_PREFIX}{secrets.token_hex(3).upper()}"


def _drop(sql: DockerSql, username: str) -> None:
    """Remove every trace of a throwaway account (never raises)."""
    literal = f"_utf8mb4 X'{username.encode().hex().upper()}'"
    for statement in (
        f"DELETE FROM account_access WHERE id IN (SELECT id FROM account WHERE username = {literal})",  # noqa: E501
        f"DELETE FROM realmcharacters WHERE acctid IN (SELECT id FROM account WHERE username = {literal})",  # noqa: E501
        f"DELETE FROM account WHERE username = {literal}",
    ):
        try:
            sql.run_statement("auth", statement)
        except Exception:  # noqa: BLE001 - cleanup must never mask the real failure
            pass


def _stored(sql: DockerSql, username: str) -> tuple[str, str]:
    """`(salt_hex, verifier_hex)` as the database holds them."""
    literal = f"_utf8mb4 X'{username.encode().hex().upper()}'"
    rows = sql.query(
        "auth", f"SELECT HEX(salt), HEX(verifier) FROM account WHERE username = {literal}"
    ).splitlines()
    assert rows, f"no row for {username}"
    salt_hex, verifier_hex = rows[0].split("\t")
    return salt_hex.strip(), verifier_hex.strip()


def test_a_created_account_lands_in_the_auth_database_as_a_thirty_two_byte_row(
    sql: DockerSql,
) -> None:
    """The row we write is the shape `binary(32)` wants, and reproduces from its own salt."""
    username = _throwaway_name()
    try:
        result = accounts.create_account(sql, username, _PASSWORD)
        assert result.created is True
        assert result.username == username

        salt_hex, verifier_hex = _stored(sql, username)
        assert len(salt_hex) == 64 and len(verifier_hex) == 64  # 32 bytes each, not padded short
        recomputed = accounts.verifier_for(username, _PASSWORD, bytes.fromhex(salt_hex))
        assert recomputed.hex().upper() == verifier_hex
    finally:
        _drop(sql, username)


def test_creating_the_same_account_twice_reports_it_and_leaves_the_first_one_alone(
    sql: DockerSql,
) -> None:
    """A second attempt must not re-salt the account and lock the owner out."""
    username = _throwaway_name()
    try:
        first = accounts.create_account(sql, username, _PASSWORD)
        before = _stored(sql, username)

        second = accounts.create_account(sql, username.lower(), "Different!1")
        assert second.created is False
        assert second.account_id == first.account_id
        assert _stored(sql, username) == before  # untouched, so the old password still works
    finally:
        _drop(sql, username)


def test_an_explicit_gm_level_is_written_and_read_back(sql: DockerSql) -> None:
    username = _throwaway_name()
    try:
        result = accounts.create_account(sql, username, _PASSWORD, gm_level=3)
        rows = sql.query(
            "auth", f"SELECT gmlevel, RealmID FROM account_access WHERE id = {result.account_id}"
        ).splitlines()
        assert [cell.strip() for cell in rows[0].split("\t")] == ["3", "-1"]
        assert accounts.create_account(sql, username, _PASSWORD).gm_level == 3
    finally:
        _drop(sql, username)


def test_our_verifier_is_byte_identical_to_one_the_worldserver_wrote_itself(
    sql: DockerSql,
) -> None:
    """The checklist 6.5 gate: the SERVER writes the row, we only reproduce it.

    This is the test that can actually fail if the algorithm drifts from
    AzerothCore's — the unit vectors are a frozen copy of a run like this one.
    Needs the worldserver console, so it skips where there is no pty (Windows,
    which is exactly the platform this module exists for; run it on Linux or
    macOS, or in the Ubuntu VM the module docstring records).
    """
    world = os.environ.get(_WORLD_CONTAINER_ENV, docker_ctl.SPEC.world)
    if not console.pty_supported():
        pytest.skip("no pty here, so the worldserver console cannot be driven")
    if not _running(world):
        pytest.skip(f"the worldserver container {world!r} is not running")

    username = _throwaway_name()
    try:
        # The worldserver computes the salt and verifier; we never send either.
        console.send_command(f"account create {username} {_PASSWORD}", container=world, window=5.0)
        salt_hex, verifier_hex = _stored(sql, username)

        recomputed = accounts.verifier_for(username, _PASSWORD, bytes.fromhex(salt_hex))
        assert recomputed.hex().upper() == verifier_hex, (
            "our SRP6 no longer agrees with the server's; an account written by "
            "this module would exist and never authenticate"
        )
    finally:
        _drop(sql, username)
