"""Live-Docker exercise of `yulon.docker` and the base `Controller` (roadmap 1.5).

Runs the real `docker compose` / `docker ps` / `docker inspect` / `docker logs`
paths against the throwaway busybox project from `conftest.py`. Skipped when
no daemon is reachable. One test carries the whole lifecycle on purpose: the
steps depend on each other (you cannot check "stopped" before "started"), and
bringing the project up/down once per assertion would be slow for no gain.
"""

from __future__ import annotations

import gzip
import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.integration.conftest import (
    BUSYBOX_IMAGE,
    MARIADB_ROOT_PASSWORD,
    THROWAWAY_PORTS,
    THROWAWAY_REALM_HOST,
    THROWAWAY_REALM_PORT,
    THROWAWAY_SPEC,
    import_runs,
)
from yulon import docker, platform
from yulon.controller import Controller, PortConflictError

pytestmark = pytest.mark.integration

# Generous for a cold `busybox` pull; the happy path finishes in a few seconds.
_DB_TIMEOUT = 60.0
_READY_TIMEOUT = 60.0
_INTERVAL = 0.5


def test_docker_lifecycle_end_to_end(throwaway_project: Path) -> None:
    """start → healthy → ready → status → conflict guard → stop, for real."""
    ctl = Controller(THROWAWAY_SPEC, throwaway_project)

    # Nothing of ours is up yet, and nothing else holds our ports.
    assert ctl.status().any_running is False
    assert ctl.port_conflicts() == []

    ctl.start()
    try:
        assert ctl.wait_db_healthy(timeout=_DB_TIMEOUT, interval=_INTERVAL) is True
        assert docker.health(THROWAWAY_SPEC.db) == "healthy"
        assert (
            ctl.wait_ready(
                THROWAWAY_REALM_HOST,
                THROWAWAY_REALM_PORT,
                timeout=_READY_TIMEOUT,
                interval=_INTERVAL,
            )
            is True
        )
        assert ctl.status().all_running is True

        # The global scan sees our own published ports ...
        assert set(docker.port_conflicts(THROWAWAY_PORTS)) >= {
            THROWAWAY_SPEC.db,
            THROWAWAY_SPEC.auth,
        }
        # ... but the controller filters its own containers out, so a restart
        # of *this* install is not a conflict ...
        assert ctl.port_conflicts() == []
        ctl.start()  # idempotent `compose up -d`; must not raise

        # ... while a *different* install wanting the same ports is refused
        # before `docker compose up` ever runs (README §12).
        stranger = docker.ContainerSpec(
            db="yulon-it-other-db",
            auth="yulon-it-other-auth",
            world="yulon-it-other-world",
            ports=THROWAWAY_PORTS,
        )
        with pytest.raises(PortConflictError) as excinfo:
            Controller(stranger, throwaway_project / "nonexistent").start()
        assert set(excinfo.value.containers) >= {THROWAWAY_SPEC.db, THROWAWAY_SPEC.auth}
    finally:
        ctl.stop()

    assert ctl.status().any_running is False
    assert ctl.port_conflicts() == []
    # Stopped, but still *there*: `stop()` keeps the containers so the next
    # start can go through `docker start`. A stopped container reports the
    # health of its last run rather than `unknown` (which means "no such
    # container"), so the check here is existence, not the health string.
    assert docker.container_exists(THROWAWAY_SPEC.db) is True


def test_wait_db_healthy_times_out_on_missing_container(require_docker: None) -> None:
    """A container that never appears makes the poll return False, not hang or raise."""
    assert docker.wait_db_healthy("yulon-it-does-not-exist", timeout=1.5, interval=0.5) is False


def test_compose_up_reruns_the_one_shot_import(staged_project: Path) -> None:
    """Why `start_staged()` exists: a bare `compose up -d` runs the import again.

    This is the failure `dml-start.sh` documents in as many words. Asserting it
    against a real daemon keeps the reason for the staged path from decaying
    into a comment nobody can check.
    """
    docker.start(staged_project)
    assert import_runs(staged_project) == 1

    docker.start(staged_project)
    assert import_runs(staged_project) == 2, "expected the documented re-run"


def test_launcher_restart_never_reruns_the_one_shot_import(staged_project: Path) -> None:
    """The launcher's own stop → start cycle must not re-run the import.

    `start_staged()` alone is not enough: it takes the staged path only while
    the containers still exist, so a `stop()` that *removes* them puts the next
    start straight back on `compose up -d`. Driving `Controller` here rather
    than `yulon.docker` is the point — this is the pair of buttons a user
    presses, and the pair has to hold the invariant together.
    """
    ctl = Controller(THROWAWAY_SPEC, staged_project)

    # The INSTALLER brings the project up the first time (the bash script runs
    # `compose up -d --build`), and that is when the one-shot import runs. The
    # launcher's Start button is for an already-installed server and never
    # imports — so the install is simulated here rather than pressed.
    docker.start(staged_project)
    assert ctl.wait_db_healthy(timeout=_DB_TIMEOUT, interval=_INTERVAL) is True
    assert import_runs(staged_project) == 1

    ctl.stop()
    assert docker.container_exists(THROWAWAY_SPEC.world) is True, "stop() removed the containers"

    ctl.start()
    assert ctl.wait_db_healthy(timeout=_DB_TIMEOUT, interval=_INTERVAL) is True
    assert import_runs(staged_project) == 1, "the restart re-ran the one-shot import"


def test_a_changed_compose_file_is_applied_without_re_running_the_import(
    staged_project: Path,
) -> None:
    """The case that used to force the destructive path, now proven harmless.

    Editing a compose file must actually take effect — `docker start` would
    replay the container as created and the setting would silently do nothing.
    The old implementation achieved that by falling back to a bare
    `compose up -d`, which re-ran the one-shot import: the user changed a port
    and lost their characters. Naming the services means compose recreates the
    one that changed and never selects the import at all.
    """
    compose = staged_project / "docker-compose.yml"
    docker.start(staged_project)
    assert import_runs(staged_project) == 1

    compose.write_text(
        compose.read_text(encoding="utf-8").replace(
            "World initialized, ready...", "World initialized (edited), ready..."
        ),
        encoding="utf-8",
    )
    assert docker.start_staged(THROWAWAY_SPEC, staged_project) is True

    assert import_runs(staged_project) == 1, "the edit re-ran the one-shot import"
    world = docker.follow_logs(THROWAWAY_SPEC.world, tail=20)
    assert any("(edited)" in line for line in world), "the edit never reached the container"


def test_a_missing_container_is_recreated_without_re_running_the_import(
    staged_project: Path,
) -> None:
    """A container removed by hand is not evidence that the server was never installed.

    The old implementation equated "one of the three is missing" with "first
    install" and ran `compose up -d`, taking the database with it — while the
    persistent volume was sitting there intact.
    """
    docker.start(staged_project)
    assert import_runs(staged_project) == 1

    subprocess.run(["docker", "rm", "-f", THROWAWAY_SPEC.world], capture_output=True, check=False)
    assert docker.container_exists(THROWAWAY_SPEC.world) is False

    assert docker.start_staged(THROWAWAY_SPEC, staged_project) is True
    assert docker.container_exists(THROWAWAY_SPEC.world) is True
    assert import_runs(staged_project) == 1, "recreating a container re-ran the import"


def test_start_staged_waits_for_the_database_before_starting_the_servers(
    throwaway_project: Path,
) -> None:
    """The claim `start_staged()` gave up its own health-wait for.

    It deleted the Python polling on the grounds that compose owns the
    dependency graph and honours `condition: service_healthy` even under
    `--no-deps` — because `--no-deps` prunes only edges pointing OUTSIDE the
    selected set, and `db` is one of the three named services. Nothing
    exercised that until now; the fixture declared a healthcheck and no
    depends_on at all (review, 2026-08-22).

    The database here takes two seconds to become healthy, so if the ordering
    were not honoured the servers would start first.
    """
    assert docker.start_staged(THROWAWAY_SPEC, throwaway_project) is True

    db_started = docker.started_at(THROWAWAY_SPEC.db)
    world_started = docker.started_at(THROWAWAY_SPEC.world)
    assert db_started and world_started
    assert world_started > db_started, (
        f"world started at {world_started}, database at {db_started} — the health gate was not "
        "honoured, so a worldserver can come up against a database that is not ready"
    )
    assert docker.health(THROWAWAY_SPEC.db) == "healthy"


def test_start_staged_fails_closed_when_the_database_never_becomes_healthy(
    never_healthy_project: Path,
) -> None:
    """A worldserver started against a dead database is what the gate prevents.

    With the Python health-wait gone, compose's own `service_healthy` condition
    is the ONLY thing standing between a restart and that outcome — so it has
    to fail, loudly, rather than start the servers anyway.
    """
    with pytest.raises(docker.DockerCommandError):
        docker.start_staged(THROWAWAY_SPEC, never_healthy_project)

    running = set(docker.status())
    assert THROWAWAY_SPEC.world not in running, "started a server against an unhealthy database"
    assert THROWAWAY_SPEC.auth not in running


def test_stop_staged_reports_whether_there_was_anything_to_stop(
    throwaway_project: Path,
) -> None:
    """True means "was up, now down"; False means "there was nothing of ours".

    It used to return `compose stop`'s exit code, which is 0 for a project where
    nothing was running — so a Stop pressed on an already-stopped install said
    the same thing as one that really stopped a server (review, 2026-08-22).
    """
    assert docker.stop_staged(THROWAWAY_SPEC, throwaway_project) is False

    docker.start_staged(THROWAWAY_SPEC, throwaway_project)
    assert docker.stop_staged(THROWAWAY_SPEC, throwaway_project) is True
    assert docker.stop_staged(THROWAWAY_SPEC, throwaway_project) is False

    # And the containers survived, so the next start stays staged.
    assert docker.container_exists(THROWAWAY_SPEC.world) is True


def test_a_stop_never_writes_an_identity_the_folder_could_carry_away(
    throwaway_project: Path,
) -> None:
    """Live counterpart of the unit test: no pin is written, deliberately.

    A Stop-time pin was implemented — the census has just proved the basename
    and the labels agree, so the value would even be correct — and reverted the
    same day: `.env` travels with the folder, `install_project()` prefers it
    over the directory, so a COPY of the install inherits the original's
    identity and a Stop pressed in the copy stops the original's running
    server. Measured end to end (review, 2026-08-22). The unpinned copy fails
    closed instead, because its basename disagrees with the labels.
    """
    assert docker.pinned_project_name(throwaway_project) is None
    docker.start_staged(THROWAWAY_SPEC, throwaway_project)
    assert docker.stop_staged(THROWAWAY_SPEC, throwaway_project) is True

    assert docker.pinned_project_name(throwaway_project) is None
    assert not (throwaway_project / ".env").exists()


def test_the_bind_mount_probe_actually_works_against_a_real_daemon(
    tmp_path: Path, require_docker: None
) -> None:
    """The probe must run the image it was given, and no unit test can check that.

    This exists because of a defect that shipped and that the unit tests could
    not see. `bind_mount_ok()` passed `ls -A /probe` after the image name, and
    the probe image is the pinned `alpine/git` — chosen deliberately so the
    probe pulls the same digest the clone stages pull instead of a second
    image — whose ENTRYPOINT is `git`. So the probe ran `git ls -A /probe`,
    exited 1 with "'ls' is not a git command", and the function read that as
    "Docker cannot see this folder". `preflight` turns that into a refusal, so
    **every native install on every platform was refused** (found on
    yulon-win11 2026-08-24 by a gate asking a different question, then
    reproduced on Linux — it was never Windows-specific).

    The unit test asserted the argv, and the argv was exactly what the author
    intended; the defect lived between the argv and the image's metadata, which
    a monkeypatched `runner.run` returning a canned success can never know.
    Only a real daemon holds both halves. That is the whole reason this file
    exists, so the probe belongs in it.

    Deliberately NOT asserting the negative case (an unshared folder reporting
    False): Docker Engine on Linux shares the whole filesystem, so there is no
    unshared folder to point at here. That half was measured on Windows against
    a drive the WSL2 VM does not mount — exit 0 with an empty listing, which is
    the behaviour the comparison logic exists for — and is recorded in
    `pyplan/checklist.md` rather than asserted here.

    `require_docker` is in the signature for a reason it went without for a
    day. Every other test in this file takes `throwaway_project`, which chains
    to that gate; this one needs no compose project, so it quietly opted out of
    the promise this suite's `conftest` makes in its own docstring — "every
    test here is skipped (not failed) when `docker info` cannot reach a
    daemon". It was written and proven on a box where the daemon was up, so
    nothing showed. On a laptop with Docker Desktop installed but stopped, the
    probe returns False, the assertion fails, and the default `pytest` run is
    RED for a reason that has nothing to do with the code (found 2026-08-24 by
    running the suite on this machine).
    """
    from yulon import git

    (tmp_path / "alpha.txt").write_text("x", encoding="utf-8")
    (tmp_path / "bravo.txt").write_text("y", encoding="utf-8")
    chosen = tmp_path / "wow-server"  # not created: the probe walks up to tmp_path

    assert docker.bind_mount_ok(chosen, git.CONTAINER_GIT_IMAGE) is True
    # `-v <missing>:/probe` would have Docker create it; the probe must not.
    assert not chosen.exists()


# ------------------------------------------------ 7.3: the CMaNGOS primitives


def _busybox_containers() -> set[str]:
    """Every container, running or exited, made from the busybox image — the leak census."""
    proc = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"ancestor={BUSYBOX_IMAGE}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return set(proc.stdout.split())


def test_run_container_reads_the_client_read_only_and_writes_out_as_this_user(
    tmp_path: Path, require_docker: None
) -> None:
    """The extraction model, live: `/client` `:ro`, `/out` rw, cwd `/out`, `-u uid:gid`.

    The relative `copy.txt` proves the workdir — drop `-w` and it lands in the
    image's own cwd and `out/` stays empty — and the owner check proves no
    `sudo chown` will ever be needed. The client listing is a statement about
    THIS argv, which only reads `/client`: it would hold with the `:ro` gone
    too, so it is not what gates the mount. The test below it is, by asking a
    container to write there and being refused.

    The container is ASKED who it is (`id -u` / `id -g`) rather than trusting
    that a `--user` in the argv arrived. Two separate things want that question,
    and the first version of this test conflated them into one that gated
    neither on this machine:

    * **`to_argv()` splicing `user_args` into the argv at all.** That is what
      the `4242:4242` probe is for, and it is the same number the mutation
      control used. It runs on EVERY platform, because it names the uid itself
      rather than asking `platform` for one — busybox's default user is root, so
      a probe that passes no `--user` and a `to_argv()` that drops the one it
      was given produce the same `0 0`, and the assertion would be a fact about
      the image rather than about this module. That is exactly what the first
      version asserted on Docker Desktop: reran with `*self.user_args` deleted
      from `to_argv()`, it still passed (review, 2026-09-01).
    * **`platform.container_user_args()`'s policy**, which is what the second
      run gates and which genuinely differs per platform: `--user uid:gid` on
      Linux, deliberately none on Docker Desktop, where the image's own root is
      right and the file still arrives owned by the logged-in user. On Docker
      Desktop the `0 0` there is a statement about the policy being empty — it
      is TRUE of a broken `to_argv()` too, and no longer has to carry that
      weight now that the probe above does.

    A host-side `st_uid` check cannot stand in for either: `os.getuid` exists
    only on Linux, so on Docker Desktop it does not run.
    """
    asked = ("--user", "4242:4242")
    probe_heard: list[str] = []
    probe = docker.run_container(
        docker.ContainerRun(
            image=BUSYBOX_IMAGE, argv=("sh", "-c", "id -u; id -g"), user_args=asked
        ),
        sink=probe_heard.append,
    )
    assert probe.returncode == 0, probe.tail
    probe_reported = [line.strip() for line in probe_heard if line.strip().isdigit()]
    assert probe_reported[-2:] == ["4242", "4242"], (
        f"asked for {asked[1]} and the container reported {probe_reported[-2:]} — to_argv() is "
        f"not putting user_args in the argv: {probe_heard}"
    )

    client = tmp_path / "client"
    client.mkdir()
    (client / "a.txt").write_text("alpha\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    user_args = tuple(platform.container_user_args())
    spec = docker.ContainerRun(
        image=BUSYBOX_IMAGE,
        argv=("sh", "-c", "cat /client/a.txt > copy.txt; id -u; id -g"),
        mounts=(docker.Mount(client, "/client", read_only=True), docker.Mount(out, "/out")),
        workdir="/out",
        user_args=user_args,
    )
    heard: list[str] = []
    run = docker.run_container(spec, sink=heard.append)
    assert run.returncode == 0, run.tail
    assert (out / "copy.txt").read_text(encoding="utf-8") == "alpha\n"
    reported = [line.strip() for line in heard if line.strip().isdigit()]
    if user_args:
        assert user_args[0] == "--user", user_args
        assert ":".join(reported[-2:]) == user_args[1], heard
    else:
        # Docker Desktop: the policy is to pass none, so the image's own root
        # is the right answer. `to_argv()` is gated by the probe above, not
        # here — nothing this branch asserts depends on it.
        assert reported[-2:] == ["0", "0"], heard
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        # Linux only: Docker Desktop's file sharing maps ownership itself.
        assert (out / "copy.txt").stat().st_uid == getuid()
    assert sorted(entry.name for entry in client.iterdir()) == ["a.txt"]


def test_a_read_only_client_mount_refuses_a_write(tmp_path: Path, require_docker: None) -> None:
    """`:ro` is enforced by the kernel, not by the tool's manners — and the tail says so."""
    client = tmp_path / "client"
    client.mkdir()
    spec = docker.ContainerRun(
        image=BUSYBOX_IMAGE,
        argv=("sh", "-c", "touch /client/written"),
        mounts=(docker.Mount(client, "/client", read_only=True),),
    )
    run = docker.run_container(spec, sink=lambda _line: None)
    assert run.returncode != 0
    assert "Read-only file system" in " ".join(run.tail)
    assert not (client / "written").exists()


def test_copy_from_image_leaves_no_container_behind_either_way(
    tmp_path: Path, require_docker: None
) -> None:
    """A real create/cp/rm, then the failure path, and the census is unchanged after both.

    The pull is not politeness: `--filter ancestor=<image>` needs the image to
    exist locally, and a census taken against an image this daemon has never
    seen answers "no containers" for every one of them, leak included.
    """
    subprocess.run(["docker", "pull", BUSYBOX_IMAGE], capture_output=True, check=False)
    before = _busybox_containers()
    dest = tmp_path / "passwd"
    docker.copy_from_image(BUSYBOX_IMAGE, "/etc/passwd", dest)
    assert dest.is_file()
    assert "root:" in dest.read_text(encoding="utf-8")
    with pytest.raises(docker.DockerCommandError):
        docker.copy_from_image(BUSYBOX_IMAGE, "/no/such/path", tmp_path / "nope")
    assert not (tmp_path / "nope").exists()
    assert _busybox_containers() == before


def test_exec_stdin_streams_a_gzipped_dump_and_sql_query_reads_it_back(
    tmp_path: Path, mariadb_container: str
) -> None:
    """The SQL transport against a real client: gzip in, rows out, password never printed.

    A gzip source is the load-bearing choice: if `exec_stdin()` ever handed the
    child the stream's `fileno()`, mariadb would receive compressed bytes and
    this would fail on the first statement.
    """
    dump = tmp_path / "seed.sql.gz"
    with gzip.open(dump, "wb") as compressed:
        compressed.write(
            b"CREATE DATABASE yulon_it;\n"
            b"CREATE TABLE yulon_it.t (n INT);\n"
            b"INSERT INTO yulon_it.t VALUES (1),(2),(3);\n"
        )
    with gzip.open(dump, "rb") as source:
        proc = docker.exec_stdin(
            mariadb_container,
            ["mariadb", "-u", "root"],
            source,
            env={"MYSQL_PWD": MARIADB_ROOT_PASSWORD},
        )
    assert proc.returncode == 0, proc.stderr
    # Amendment A11 against a real daemon: what ran carries the variable's NAME.
    assert MARIADB_ROOT_PASSWORD not in " ".join(proc.args)
    assert "MYSQL_PWD" in proc.args, proc.args
    assert f"MYSQL_PWD={MARIADB_ROOT_PASSWORD}" not in proc.args
    count = docker.sql_query(
        mariadb_container, "mariadb", MARIADB_ROOT_PASSWORD, "yulon_it", "SELECT COUNT(*) FROM t"
    )
    assert count.strip() == "3"
    with pytest.raises(docker.DockerCommandError) as excinfo:
        docker.sql_query(mariadb_container, "mariadb", "not-the-password", None, "SELECT 1")
    assert "Access denied" in str(excinfo.value)
    assert MARIADB_ROOT_PASSWORD not in str(excinfo.value)


def test_wait_ready_gives_up_on_a_crash_loop_long_before_its_timeout(
    crash_loop_container: str,
) -> None:
    """`RestartCount` growing past `restart_loop` is the answer, not the timeout.

    A CMaNGOS worldserver that cannot load its maps restarts forever under
    `unless-stopped`; waiting out ten minutes of that tells the user nothing
    the fourth restart had not already said. Docker's restart backoff doubles
    from 100ms, so four restarts arrive in a few seconds.
    """
    spec = docker.ReadySpec(world="ready", auth=None, timeout=90.0, interval=0.5, restart_loop=4)
    started = time.monotonic()
    assert docker.wait_ready(crash_loop_container, crash_loop_container, spec) is False
    assert time.monotonic() - started < 45.0, "the crash loop was waited out, not detected"
