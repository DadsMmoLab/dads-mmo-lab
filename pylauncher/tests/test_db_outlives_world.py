"""T98: the database outlives the world server on every stop path, not only the app's.

Measured on yulon-ubuntu (2026-09-23): the app's Stop (`compose stop -t 300`) walks `depends_on`
and stops the database last, but a Docker DAEMON shutdown — a PC shutdown, a Docker Desktop quit,
`systemctl stop docker` — signals every container at once. MariaDB was gone 1.8 s later while the
world was still draining its save queue: 0 saves landed, 9 failed. On Tortoise the character tables
are MyISAM, so a save cut between its DELETE and its INSERT leaves a character without those rows.

So the database service's entrypoint holds SIGTERM while a server container is still connected and
only then shuts the database down, and its `stop_grace_period` is long enough for that wait. These
tests run the RENDERED script (compose's `$$` unescaped, exactly what the container's bash gets)
with the image's entrypoint and SQL client stubbed, which is the real path minus the database.
"""

from __future__ import annotations

import re
import signal
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from tests.support_bash import bash_available
from yulon import docker, resources
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, load_catalog

TEMPLATES = resources.installers_dir()
GAMES = ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise")

# Which SQL client and which password variable each image family's script must use: the official
# mariadb images read MARIADB_ROOT_PASSWORD and ship `mariadb`; mysql:8.4 reads
# MYSQL_ROOT_PASSWORD and ships `mysql`. A script naming the other one fails its query, and the
# failure path shuts the database down at once -- which would quietly undo the whole fix.
CLIENT = {
    "mariadb": ("mariadb", "MARIADB_ROOT_PASSWORD"),
    "mysql": ("mysql", "MYSQL_ROOT_PASSWORD"),
}


def _render(entry: CatalogEntry, tmp_path: Path) -> dict:
    plan = composegen.render(
        entry,
        tmp_path / "wow",
        templates_root=TEMPLATES,
        db_password="t98-password-for-tests",
        platform_id=lambda: "linux",
    )
    return yaml.safe_load(plan.base)


def _db_and_world(entry: CatalogEntry, tmp_path: Path) -> tuple[dict, dict]:
    services = _render(entry, tmp_path)["services"]
    return services[entry.containers.db], services[entry.containers.world]


def _seconds(duration: str) -> int:
    """Compose's duration syntax as the templates spell it (`5m`, `90s`, `1m30s`)."""
    parts = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", duration)
    assert parts and duration, f"unreadable duration {duration!r}"
    return int(parts.group(1) or 0) * 60 + int(parts.group(2) or 0)


def _script(db: dict) -> str:
    """The shell text the container runs, after compose's own `$$` -> `$` unescaping."""
    entrypoint = db["entrypoint"]
    assert entrypoint[:2] == ["bash", "-c"], entrypoint
    return entrypoint[2].replace("$$", "$")


def _family(db: dict) -> str:
    return "mysql" if db["image"].startswith("mysql:") else "mariadb"


@pytest.mark.parametrize("game", GAMES)
def test_the_database_grace_covers_the_world_grace(game: str, tmp_path: Path) -> None:
    """A daemon shutdown SIGKILLs a container at ITS OWN grace, so the DB's must not be shorter."""
    db, world = _db_and_world(load_catalog().get(game), tmp_path)
    assert "stop_grace_period" in db, "the database takes Docker's 10 s default"
    assert _seconds(db["stop_grace_period"]) >= _seconds(world["stop_grace_period"])


@pytest.mark.parametrize("game", GAMES)
def test_the_database_waits_for_the_servers_inside_every_stop_grace(
    game: str, tmp_path: Path
) -> None:
    """The wait is bounded well inside the 300 s every one of the app's own stops passes.

    `compose stop -t 300` and `docker stop -t 300` override `stop_grace_period` for every
    service, so a wait that could run to 300 s would be SIGKILLed on the app's path while holding
    for an outside client (a SQL tool on the published port). 60 s is left for the database's own
    shutdown, which was measured at 1.4-2.6 s.
    """
    db, _ = _db_and_world(load_catalog().get(game), tmp_path)
    bound = re.search(r"-lt (\d+) ", _script(db))
    assert bound, "the wait has no visible bound"
    assert int(bound.group(1)) + 60 <= docker.STOP_GRACE_SECONDS
    assert int(bound.group(1)) + 60 <= _seconds(db["stop_grace_period"])


@pytest.mark.parametrize("game", GAMES)
def test_the_wrapper_runs_the_images_own_entrypoint_and_server(game: str, tmp_path: Path) -> None:
    """Setting `entrypoint:` clears the image's CMD, so the server binary must be named again."""
    db, _ = _db_and_world(load_catalog().get(game), tmp_path)
    family = _family(db)
    assert db["command"] == ["mariadbd" if family == "mariadb" else "mysqld"]
    script = _script(db)
    assert 'docker-entrypoint.sh "$@" &' in script
    client, password = CLIENT[family]
    assert f'MYSQL_PWD="${password}" {client} ' in script


# --- behaviour: the rendered script under bash, with the database stubbed ---------------------

needs_bash = pytest.mark.skipif(
    not bash_available(), reason="no bash that can run a script on this machine"
)

FAKE_SERVER = """#!/usr/bin/env bash
# Stands in for the image's docker-entrypoint.sh after it has exec'd the server: runs until
# SIGTERM, records when that came, exits 0 -- a clean shutdown.
trap 'echo term >> "$T98_DIR/server.log"; exit 0' TERM
echo started >> "$T98_DIR/server.log"
while :; do sleep 0.05; done
"""

FAKE_CLIENT = """#!/usr/bin/env bash
# Stands in for the SQL client: prints how many server connections are open, or fails like a
# database that is not answering -- with something on stdout, so the script must go by the exit
# status and not by what it read. Records the query so the test can see what was asked.
echo "$@" >> "$T98_DIR/queries.log"
[ -f "$T98_DIR/unreachable" ] && { echo "ERROR 2002 (HY000)"; exit 1; }
cat "$T98_DIR/connections"
"""


def _start(game: str, tmp_path: Path, *, widen: bool = False) -> tuple[subprocess.Popen[str], Path]:
    db, _ = _db_and_world(load_catalog().get(game), tmp_path)
    client, _ = CLIENT[_family(db)]
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, text in (("docker-entrypoint.sh", FAKE_SERVER), (client, FAKE_CLIENT)):
        path = stubs / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    (tmp_path / "connections").write_text("1\n", encoding="utf-8")
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "T98_DIR": str(tmp_path)}
    script = _script(db)
    if widen:
        script = _widen_the_early_window(script)
    proc = subprocess.Popen(["bash", "-c", script, "yulon-db", *db["command"]], env=env, text=True)
    if not widen:
        _until(lambda: "started" in _log(tmp_path), "the server never started")
    return proc, tmp_path


def _widen_the_early_window(script: str) -> str:
    """Hold the script for a second between installing its trap and starting the server.

    A signal in that window is real -- Docker can stop a container the instant it starts -- but
    it lasts microseconds, so no test could land in it by timing. The one line added here marks
    the window (`trapped`) and sleeps in it; everything else is the rendered script unchanged.
    """
    trap = "trap hold TERM INT\n"
    assert script.count(trap) == 1, "the trap line moved; this test must move with it"
    return script.replace(trap, trap + ': > "$T98_DIR/trapped"; sleep 1\n')


def _log(where: Path) -> str:
    path = where / "server.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _until(check, what: str, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(what)


@needs_bash
@pytest.mark.parametrize("game", ("wow-wotlk", "wow-tortoise"))
def test_sigterm_is_held_while_a_server_is_connected(game: str, tmp_path: Path) -> None:
    proc, where = _start(game, tmp_path)
    try:
        proc.send_signal(signal.SIGTERM)
        time.sleep(2.5)
        assert "term" not in _log(where), "the database was stopped under a connected world"
        assert proc.poll() is None
        (where / "connections").write_text("0\n", encoding="utf-8")
        _until(lambda: "term" in _log(where), "the database was never stopped")
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()


@needs_bash
@pytest.mark.parametrize("game", ("wow-wotlk", "wow-tortoise"))
def test_with_no_server_connected_the_database_stops_at_once(game: str, tmp_path: Path) -> None:
    """The app's own Stop reaches the DB after the world is gone: nothing may be added there."""
    proc, where = _start(game, tmp_path)
    try:
        (where / "connections").write_text("0\n", encoding="utf-8")
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
        assert time.monotonic() - started < 2.0
        assert "term" in _log(where)
    finally:
        proc.kill()


@needs_bash
@pytest.mark.parametrize("game", ("wow-wotlk", "wow-tortoise"))
def test_a_database_that_cannot_be_asked_is_stopped_rather_than_held(
    game: str, tmp_path: Path
) -> None:
    """A failing count must never turn into a wait: that would be a SIGKILL at the grace."""
    proc, where = _start(game, tmp_path)
    try:
        (where / "unreachable").write_text("", encoding="utf-8")
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=5) == 0
        assert "term" in _log(where)
    finally:
        proc.kill()


@needs_bash
def test_the_count_leaves_out_this_containers_own_connections(tmp_path: Path) -> None:
    """Loopback and socket clients are the app's own `docker exec` queries and the healthcheck,
    not a server; counting them would hold every stop for the full bound."""
    proc, where = _start("wow-tortoise", tmp_path)
    try:
        (where / "connections").write_text("0\n", encoding="utf-8")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        proc.kill()
    query = (where / "queries.log").read_text(encoding="utf-8")
    assert "ID <> CONNECTION_ID()" in query
    assert "HOST LIKE '%:%'" in query  # TCP only: a socket client's HOST is plain `localhost`
    assert "HOST NOT LIKE '127.%'" in query


@needs_bash
@pytest.mark.parametrize("game", ("wow-wotlk", "wow-tortoise"))
def test_a_signal_before_the_server_started_still_stops_it(game: str, tmp_path: Path) -> None:
    """The early-signal line: TERM before `db=$!` exists must reach the server once it does.

    Without it `hold()` runs with no server to signal (the count fails -- nothing is up yet --
    so it gives up at once), the server then starts, and nothing ever stops it: the container
    is SIGKILLed at the end of its grace, which is the abrupt stop this whole change exists
    to prevent.
    """
    (tmp_path / "unreachable").write_text("", encoding="utf-8")
    proc, where = _start(game, tmp_path, widen=True)
    try:
        _until(lambda: (where / "trapped").exists(), "the script never reached its trap")
        proc.send_signal(signal.SIGTERM)
        # Any exit status: the signal can reach the stub server before its own trap is set, and
        # then it dies of SIGTERM rather than exiting 0 -- a real entrypoint would too. What
        # matters is that the script is not left holding a running server.
        proc.wait(timeout=10)
        still = subprocess.run(
            ["pgrep", "-f", str(where / "bin" / "docker-entrypoint.sh")],
            capture_output=True,
            text=True,
        )
        assert still.stdout == "", "the server outlived the script that should have stopped it"
    finally:
        proc.kill()
        subprocess.run(["pkill", "-f", str(where / "bin" / "docker-entrypoint.sh")])
