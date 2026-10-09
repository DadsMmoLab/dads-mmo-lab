"""T568: what a reservation of the server (`docker.server_claim()`) does to the commands and
presses that take it: lifecycle commands, the engine's presses, the Applier's SQL.

The primitive itself is `test_server_claim.py`'s. The docker CLI is `support_fake_docker`'s.
"""

from __future__ import annotations

import ast
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import HANG_BOUND
from tests.support_fake_docker import calls as fake_calls
from tests.support_fake_docker import containers as fake_containers
from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from tests.support_native import Recorder, engine
from tests.test_apply import STACKABLES, _FakeGit, _FakeSql
from yulon import docker, forgetting, platform
from yulon.apply import Applier, ApplyRefusal
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.manifest import parse_manifest

IMAGE = "yulon.local/wotlk-server:native"
HOLDER_PRESS = "Update the server to latest…"


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text(IMAGE + "\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


@pytest.fixture
def server(tmp_path: Path) -> Path:
    folder = tmp_path / "server"
    folder.mkdir()
    return folder


def _name(server: Path) -> str:
    ident = docker.folder_id(server)
    assert ident is not None
    return docker.SERVER_CLAIM_PREFIX + ident


def _wait_for(state: Path, name: str, there: bool) -> None:
    deadline = time.monotonic() + HANG_BOUND
    while (name in fake_containers(state)) != there:
        assert time.monotonic() < deadline, f"{name} {'never came' if there else 'never went'}"
        time.sleep(0.02)


def _another_yulon_holds(
    state: Path, name: str, owner: str = "someone-else"
) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [
            str(state.parent / "fake-docker"),
            "run", "--rm", "-i", "--name", name,
            "--label", f"{docker.OWNER_LABEL}={owner}",
            "--label", f"{docker.CLAIM_LABEL}=theirs",
            "--label", f"{docker.PRESS_LABEL}={HOLDER_PRESS}",
            "--label", f"{docker.WHO_LABEL}=pk@THEIR-PC (Windows)",
            "--label", f"{docker.PID_LABEL}=999",
            "--entrypoint", "sh", IMAGE, "-c", "cat >/dev/null",
        ],
        stdin=subprocess.PIPE,
    )  # fmt: skip
    _wait_for(state, name, there=True)
    return proc


SPEC = docker.ContainerSpec(db="ac-database", auth="ac-auth", world="ac-world", ports=(1,))

ran: list[str] = []


@docker._a_lifecycle_command
def start_staged(
    spec: docker.ContainerSpec, server_dir: Path, *, wsl_distro: str | None = None
) -> None:
    ran.append("start")


@docker._a_lifecycle_command
def recreate_staged(
    spec: docker.ContainerSpec, server_dir: Path, *, wsl_distro: str | None = None
) -> None:
    ran.append("recreate")


@docker._a_lifecycle_command
def stop_staged(
    spec: docker.ContainerSpec, server_dir: Path, *, wsl_distro: str | None = None
) -> None:
    ran.append("stop")


@pytest.fixture(autouse=True)
def _nothing_ran() -> None:
    ran.clear()


def _compose_calls(state: Path) -> list[str]:
    return [line for line in fake_calls(state) if line.startswith("compose")]


# ------------------------------------------------------------------ the default


def test_the_app_ships_with_reservations_on() -> None:
    """The suite turns them off (conftest); the module's own default must be on.

    A guard against way 5 of `nine-ways-a-test-proves-nothing`: an autouse fixture that
    neutralises the behaviour under test must not be the only thing that says it exists.
    """
    source = Path(docker.__file__).read_text(encoding="utf-8")
    (default,) = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RESERVATIONS_ON" for t in node.targets)
    ]
    assert isinstance(default.value, ast.Constant) and default.value.value is True


# ------------------------------------------------------------------ lifecycle commands


def test_start_refuses_under_another_yulons_reservation_and_runs_nothing(
    fake_docker: Path, server: Path
) -> None:
    theirs = _another_yulon_holds(fake_docker, _name(server))
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            start_staged(SPEC, server)
        assert ran == []
        assert refused.value.holder.press == HOLDER_PRESS
        assert "press “Start” again" in str(refused.value)
        assert _compose_calls(fake_docker) == []
    finally:
        theirs.kill()


@pytest.mark.parametrize("command", [recreate_staged, start_staged])
def test_every_start_like_command_refuses_the_same_way(
    fake_docker: Path, server: Path, command: Any
) -> None:
    theirs = _another_yulon_holds(fake_docker, _name(server))
    try:
        with pytest.raises(docker.ServerReserved):
            command(SPEC, server)
        assert ran == []
    finally:
        theirs.kill()


def test_a_command_inside_this_processs_own_reservation_runs_in_it(
    fake_docker: Path, server: Path
) -> None:
    """A press's own stop and recreate share its reservation: one `docker run` in all."""
    with docker.server_claim(server, press="Rebuild the server…", images=[IMAGE]):
        start_staged(SPEC, server)
        stop_staged(SPEC, server)
    assert ran == ["start", "stop"]
    runs = [line for line in fake_calls(fake_docker) if line.startswith("run ")]
    assert len(runs) == 1


def test_a_command_alone_reserves_for_its_own_length_and_lets_go(
    fake_docker: Path, server: Path
) -> None:
    start_staged(SPEC, server)
    assert ran == ["start"]
    _wait_for(fake_docker, _name(server), there=False)


def test_after_the_reservation_is_lost_nothing_is_started_but_a_stop_still_stops(
    fake_docker: Path, server: Path
) -> None:
    """The holder's rollback must not bring an old build back up under another Yu'lon's Stop."""
    name = _name(server)
    with docker.server_claim(server, press="Rebuild the server…", images=[IMAGE]) as held:
        cli = int((fake_docker / "containers" / name).read_text(encoding="utf-8"))
        import os
        import signal

        os.kill(cli, signal.SIGKILL)
        assert held.lost.wait(HANG_BOUND)
        for command in (start_staged, recreate_staged):
            with pytest.raises(docker.ServerHeldError) as refused:
                command(SPEC, server)
            assert str(refused.value) == docker.LOST_RESERVATION
        assert ran == []
        stop_staged(SPEC, server)
        assert ran == ["stop"]


def test_the_in_process_hold_is_still_answered_first(fake_docker: Path, server: Path) -> None:
    with docker.hold_the_server(server, "A restore is writing here."):
        with pytest.raises(docker.ServerHeldError) as refused:
            start_staged(SPEC, server)
    assert str(refused.value) == "A restore is writing here."
    assert not isinstance(refused.value, docker.ServerReserved)
    assert [line for line in fake_calls(fake_docker) if line.startswith("run ")] == []


def test_a_docker_that_is_not_there_does_not_refuse_a_start_it_will_fail_itself(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no docker CLI the command runs and meets that in its own typed words.

    Mutation this catches: a `moot` reservation refused, which put "could not reserve" in
    front of the Docker banner's advice (`DockerCliMissingError`).
    """
    monkeypatch.setattr(platform, "docker_program", lambda: None)
    start_staged(SPEC, server)
    assert ran == ["start"]


def test_a_daemon_that_is_down_does_not_refuse_a_start_it_will_fail_itself(
    fake_docker: Path, server: Path
) -> None:
    (fake_docker / "no-answer").write_text("", encoding="utf-8")  # "Cannot connect to the daemon"
    start_staged(SPEC, server)
    assert ran == ["start"]


def test_a_daemon_that_hangs_refuses_a_start_and_lets_a_stop_through(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opus adversarial review: a hung look went ahead unreserved, beside another Yu'lon's job.

    Mutation this catches: the timeout treated as moot in `_in_flight`.
    """
    (fake_docker / "inspect-hangs").write_text("", encoding="utf-8")
    monkeypatch.setattr(docker, "_CLAIM_ASK_TIMEOUT", 0.3)
    with pytest.raises(docker.ServerReservationUnavailable, match="not answering"):
        start_staged(SPEC, server)
    assert ran == []
    stop_staged(SPEC, server)  # Stop always stops
    assert ran == ["stop"]


def test_a_folder_that_takes_no_id_file_does_not_refuse_a_start(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "folder_id", lambda _folder: None)
    start_staged(SPEC, server)
    assert ran == ["start"]


def test_docker_up_but_no_image_to_reserve_with_refuses_a_start(
    fake_docker: Path, server: Path
) -> None:
    (fake_docker / "missing-images").write_text(IMAGE, encoding="utf-8")
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        start_staged(SPEC, server)
    assert refused.value.moot is False
    assert ran == []


def test_a_backup_or_restore_with_a_spec_reserves_the_server_and_without_one_does_not(
    fake_docker: Path, server: Path
) -> None:
    with docker.hold_the_server(server, "a restore", press="Restore", spec=SPEC):
        assert docker.reservation_held_here(server)
    with docker.maintenance_lease(server, "a backup", press="Backup", spec=SPEC):
        assert docker.reservation_held_here(server)
    _wait_for(fake_docker, _name(server), there=False)
    with docker.hold_the_server(server, "a restore"):
        assert not [c for c in fake_calls(fake_docker) if c.startswith("run ")][2:]


def test_a_restore_under_another_yulons_reservation_is_refused(
    fake_docker: Path, server: Path
) -> None:
    theirs = _another_yulon_holds(fake_docker, _name(server))
    try:
        with pytest.raises(docker.ServerReserved):
            with docker.hold_the_server(server, "a restore", press="Restore", spec=SPEC):
                pytest.fail("a restore went ahead under another Yu'lon's hold")
        # and the in-process hold it took first is let go again
        with docker.hold_the_server(server, "a later one"):
            pass
    finally:
        theirs.kill()


# ------------------------------------------------------------------ Stop always stops


def test_a_stop_under_another_yulons_reservation_asks_and_runs_nothing(
    fake_docker: Path, server: Path
) -> None:
    theirs = _another_yulon_holds(fake_docker, _name(server))
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            stop_staged(SPEC, server)
        assert ran == []
        holder = refused.value.holder
        assert holder.container and not holder.here
        assert holder.press == HOLDER_PRESS and "THEIR-PC" in holder.who
    finally:
        theirs.kill()


def test_stop_anyway_removes_exactly_the_shown_id_then_stops(
    fake_docker: Path, server: Path
) -> None:
    name = _name(server)
    theirs = _another_yulon_holds(fake_docker, name)
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            stop_staged(SPEC, server)
        holder = refused.value.holder
        removals = len([c for c in fake_calls(fake_docker) if c.startswith("rm ")])

        assert docker.end_reservation(holder) is True

        (removal,) = [c for c in fake_calls(fake_docker) if c.startswith("rm ")][removals:]
        assert removal == f"rm -f {name}-id", "removed by name, not by the id it was read with"
        assert name not in fake_containers(fake_docker)
        stop_staged(SPEC, server)
        assert ran == ["stop"]
    finally:
        theirs.kill()


def test_a_holder_docker_would_not_name_removes_nothing(fake_docker: Path) -> None:
    unnamed = docker.ServerHolder("yulon-busy-x", "", known=False)
    assert docker.end_reservation(unnamed) is False
    assert [c for c in fake_calls(fake_docker) if c.startswith("rm ")] == []


def test_a_stop_that_cannot_reserve_goes_ahead_and_a_start_does_not(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open for Stop only, and Stop waits only `_STOP_RESERVE_TIMEOUT` for the claim."""
    asked: list[float | None] = []
    real = docker.server_claim

    @contextmanager
    def unavailable(*args: Any, **kwargs: Any) -> Iterator[Any]:
        asked.append(kwargs.get("up_timeout"))
        raise docker.ServerReservationUnavailable("Docker would not make it.")
        yield  # pragma: no cover

    monkeypatch.setattr(docker, "server_claim", unavailable)
    stop_staged(SPEC, server)
    assert ran == ["stop"]
    with pytest.raises(docker.ServerReservationUnavailable):
        start_staged(SPEC, server)
    assert ran == ["stop"]
    assert asked == [docker._STOP_RESERVE_TIMEOUT, None]
    assert real is not unavailable


# ------------------------------------------------------------------ the engine's presses


def _installed(tmp_path: Path) -> Path:
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    (server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")
    return server_dir


def _refusing(taken: list[str]) -> Any:
    @contextmanager
    def claim(server_dir: Path, *, press: str, **_kw: Any) -> Iterator[None]:
        taken.append(press)
        raise docker.ServerReserved(
            forgetting.server_busy_elsewhere(
                "WoW", HOLDER_PRESS, "14:02 (3 minutes ago)", "pk@PC (WSL)", press
            ),
            docker.ServerHolder("yulon-busy-x", "id", press=HOLDER_PRESS, who="pk@PC (WSL)"),
        )
        yield

    return claim


PRESSES: list[tuple[str, dict[str, Any], str]] = [
    ("rebuild", {}, native.server_build_presses.REBUILD),
    ("update_to_latest", {}, native.server_build_presses.UPDATE_TO_LATEST),
    ("update_to_latest", {"to_pin": True}, native.server_build_presses.RETURN_TO_PIN),
    ("update_databases", {}, native.UPDATES_BUTTON_LABEL),
    ("repair_database", {}, native.REPAIR_DATABASE_PRESS),
    ("adopt_as_imported", {}, native.ADOPT_BUTTON_LABEL),
    ("apply_corrections", {"check": object()}, native.CORRECTIONS_BUTTON_LABEL),
]


@pytest.mark.parametrize(("method", "extra", "press"), PRESSES)
def test_every_press_refuses_under_another_yulons_reservation_before_it_sends_anything(
    tmp_path: Path, method: str, extra: dict[str, Any], press: str
) -> None:
    taken: list[str] = []
    rec = Recorder()
    installer = engine(rec, server_claim=_refusing(taken))
    server_dir = _installed(tmp_path)

    with pytest.raises(InstallerError) as refused:
        list(getattr(installer, method)(options=InstallOptions(server_dir=server_dir), **extra))
    # Which rule fired: the reservation's, under the press's own name -- not one of the
    # press's own refusals (an empty state would give those).
    assert taken == [press]
    assert "Another Yu'lon is working on WoW" in str(refused.value), str(refused.value)
    assert rec.calls == [], "the press sent something past the refusal"


def test_a_press_whose_reservation_is_moot_runs_unreserved(tmp_path: Path) -> None:
    @contextmanager
    def no_docker(server_dir: Path, *, press: str, **_kw: Any) -> Iterator[None]:
        raise docker.ServerReservationUnavailable("Docker is not running.", moot=True)
        yield

    installer = engine(Recorder(), server_claim=no_docker)
    ran_inside: list[str] = []
    with installer._reservation(_installed(tmp_path), "Rebuild"):
        ran_inside.append("body")
    assert ran_inside == ["body"]


def test_a_press_whose_reservation_cannot_be_made_for_a_real_reason_is_refused(
    tmp_path: Path,
) -> None:
    @contextmanager
    def no_image(server_dir: Path, *, press: str, **_kw: Any) -> Iterator[None]:
        raise docker.ServerReservationUnavailable("Docker has none of the images.")
        yield

    installer = engine(Recorder(), server_claim=no_image)
    with pytest.raises(InstallerError, match="none of the images"):
        with installer._reservation(_installed(tmp_path), "Rebuild"):
            pytest.fail("the body ran")


def test_a_press_on_a_folder_with_no_record_reserves_nothing(tmp_path: Path) -> None:
    """A folder Yu'lon never built is nobody's server: no id file is written into it."""
    taken: list[str] = []
    rec = Recorder()
    installer = engine(rec, server_claim=_refusing(taken))
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(InstallerError) as refused:
        list(installer.rebuild(options=InstallOptions(server_dir=bare)))
    assert taken == []
    assert "another Yu'lon" not in str(refused.value).lower()
    assert not (bare / docker.FOLDER_ID_FILE).exists()


def test_a_press_inside_another_press_shares_its_reservation(
    fake_docker: Path, tmp_path: Path
) -> None:
    """Update -> its own Rebuild: the nested `_reservation` is the same one container."""
    installer = engine(Recorder())
    server_dir = _installed(tmp_path)
    with installer._reservation(server_dir, "Outer"):
        with installer._reservation(server_dir, "Inner"):
            runs = [c for c in fake_calls(fake_docker) if c.startswith("run ")]
            assert len(runs) == 1 and "yulon.press=Outer" in runs[0]
    _wait_for(fake_docker, _name(server_dir), there=False)


def test_a_server_in_a_wsl_distro_reserves_on_that_distros_docker() -> None:
    claim = native.Seams.in_wsl("dml-arch").server_claim
    assert claim.keywords == {"wsl_distro": "dml-arch"}  # type: ignore[attr-defined]


def test_our_own_files_include_the_folder_id() -> None:
    """A folder holding only `.yulon-folder-id` is still empty to the install's guard."""
    assert docker.FOLDER_ID_FILE in native.OUR_OWN_FILES


# ------------------------------------------------------------------ the Applier's SQL


class _Spy:
    """The order of the hold, the world readings and the statements."""

    def __init__(self) -> None:
        self.events: list[str] = []

    @contextmanager
    def hold(self, press: str) -> Iterator[None]:
        self.events.append(f"hold:{press}")
        try:
            yield
        finally:
            self.events.append("release")

    def world_running(self) -> bool:
        self.events.append("world?")
        return False


class _SpySql(_FakeSql):
    def __init__(self, spy: _Spy) -> None:
        super().__init__()
        self.spy = spy

    def run_file(self, db: str, path: Path) -> None:
        self.spy.events.append("sql")
        super().run_file(db, path)

    def run_statement(self, db: str, statement: str) -> None:
        self.spy.events.append("sql")
        super().run_statement(db, statement)


def _stackables(tmp_path: Path, spy: _Spy, *, hold: Any = "spy") -> tuple[Applier, _SpySql]:
    source = parse_manifest(STACKABLES).source
    assert source is not None
    sql = _SpySql(spy)
    git = _FakeGit(
        {"up.sql": "UPDATE item_template SET stackable = 200;\n", "down.sql": "-- d\n"},
        unmodified=True,
        no_local_commits=True,
    )
    return (
        Applier(
            tmp_path,
            git=git,
            sql=sql,
            world_running=spy.world_running,
            remote_url=lambda _dest: source.url,
            hold_server=spy.hold if hold == "spy" else hold,
        ),
        sql,
    )


def test_a_direct_sql_action_holds_the_server_from_before_the_first_world_reading(
    tmp_path: Path,
) -> None:
    spy = _Spy()
    applier, sql = _stackables(tmp_path, spy)
    applier.install(parse_manifest(STACKABLES))

    assert spy.events[0] == "hold:Install All Stackables"
    assert spy.events.index("world?") < spy.events.index("sql")
    assert spy.events[-1] == "release"
    assert spy.events.count("hold:Install All Stackables") == 1
    assert sql.files and "release" not in spy.events[: spy.events.index("sql")]
    # Released only after the last statement: nothing is sent once it is let go.
    assert spy.events.index("release") > max(i for i, e in enumerate(spy.events) if e == "sql")


def test_a_reservation_held_elsewhere_refuses_the_sql_and_sends_nothing(tmp_path: Path) -> None:
    spy = _Spy()

    @contextmanager
    def held_elsewhere(press: str) -> Iterator[None]:
        raise docker.ServerReserved(
            "Another Yu'lon is working on WoW right now: “Install All Stackables”. "
            "Nothing was changed.",
            docker.ServerHolder("yulon-busy-x", "id"),
        )
        yield

    applier, sql = _stackables(tmp_path, spy, hold=held_elsewhere)
    with pytest.raises(ApplyRefusal) as refused:
        applier.install(parse_manifest(STACKABLES))
    assert "Another Yu'lon is working on WoW" in str(refused.value)
    assert sql.files == [] and sql.statements == []
    assert "world?" not in spy.events, "a reading was taken before the hold"


def test_an_action_with_no_direct_sql_takes_no_hold(tmp_path: Path) -> None:
    spy = _Spy()
    applier, _sql = _stackables(tmp_path, spy)
    manifest = parse_manifest(STACKABLES)
    # The same manifest asked for an action none of its SQL steps belong to.
    applier._sql(manifest, tmp_path, {}, "configure", applier_log(applier))
    assert [e for e in spy.events if e.startswith("hold")] == []


def applier_log(applier: Applier) -> Any:
    from yulon.apply import _Log

    return _Log()


def test_the_real_hold_refuses_the_sql_while_another_yulon_holds_the_server(
    fake_docker: Path, server: Path, tmp_path: Path
) -> None:
    """T599's shape: two Yu'lons on one server, the second one's SQL press is refused."""
    theirs = _another_yulon_holds(fake_docker, _name(server))
    spy = _Spy()

    def hold(press: str) -> Any:
        return docker.server_claim(server, press=press, images=[IMAGE], label="WoW")

    try:
        applier, sql = _stackables(tmp_path / "mods", spy, hold=hold)
        with pytest.raises(ApplyRefusal) as refused:
            applier.install(parse_manifest(STACKABLES))
        assert HOLDER_PRESS in str(refused.value) and "THEIR-PC" in str(refused.value)
        assert sql.files == [] and sql.statements == []
        assert "world?" not in spy.events
        assert _name(server) in fake_containers(fake_docker), "their reservation was removed"
    finally:
        theirs.kill()


# ------------------------------------------------------------------ install, and the classification


def test_an_install_reserves_at_start_db_and_not_before(tmp_path: Path) -> None:
    """Before `start-db` there is no database and no container to race on, and WotLK's
    `clone-core` empties the folder (so the folder id would change under a held hold)."""
    from tests.support_native import install

    rec = Recorder()

    @contextmanager
    def claim(server_dir: Path, *, press: str, **_kw: Any) -> Iterator[None]:
        rec.calls.append(f"reserve:{press}")
        yield

    install(rec, tmp_path / "wow", server_claim=claim)

    assert rec.calls.count(f"reserve:{native.INSTALL_PRESS}") == 1
    at = rec.calls.index(f"reserve:{native.INSTALL_PRESS}")
    assert rec.calls[at + 1] == "start-db", rec.calls
    assert "start-db" not in rec.calls[:at] and "build" in rec.calls[:at], rec.calls


def test_an_install_refused_by_another_yulon_stops_before_the_database(tmp_path: Path) -> None:
    from tests.support_native import install

    rec = Recorder()
    with pytest.raises(InstallerError, match="Another Yu'lon is working on"):
        install(rec, tmp_path / "wow", server_claim=_refusing([]))
    assert "start-db" not in rec.calls and "one-shot:ac-db-import" not in rec.calls


def test_every_press_of_the_engine_is_classified() -> None:
    """A press added to the engine's protocol must say how it is reserved: a guard over the
    protocol (`defects-live-between-the-parts`), not over the presses we remembered."""
    import inspect

    from yulon.catalog.families.trinitycore import TrinityCoreInstaller
    from yulon.catalog.installer import InstallEngine
    from yulon.catalog.native import StagedInstaller

    presses = {
        name
        for name, member in vars(InstallEngine).items()
        if not name.startswith("_")
        and callable(member)
        and inspect.signature(member).return_annotation == "Iterator[str]"
    }
    reserved = {
        "rebuild",
        "update_databases",
        "update_to_latest",
        "apply_corrections",
        "adopt_as_imported",
    }
    # `run` reserves at its `start-db` stage (`_staged`); nothing else is a generator press.
    assert presses == reserved | {"run"}, presses ^ (reserved | {"run"})
    for name in reserved | {"repair_database"}:
        assert hasattr(getattr(StagedInstaller, name), "__wrapped__"), f"{name} is not reserved"
    assert hasattr(TrinityCoreInstaller.reextract, "__wrapped__")
    assert not hasattr(StagedInstaller.run, "__wrapped__")


# ------------------------------------------------------------------ a lost reservation ends it
# Codex adversarial review: after "Stop anyway" removed the holder's container, a press
# kept running for the rest of its work. Its cancel now reads the reservation's loss.


class _Toy:
    """The bit of an installer `@_reserving` needs, over a reservation the test controls."""

    def __init__(self, held: docker.ClaimHeld) -> None:
        self.held = held
        self.seen: list[Any] = []

    def server_dir(self, options: InstallOptions) -> Path:
        assert options.server_dir is not None
        return options.server_dir

    @contextmanager
    def _reservation(
        self, server_dir: Path, press: str, cancel: threading.Event | None = None
    ) -> Iterator[docker.ClaimHeld]:
        yield self.held

    @native._reserving("A toy press")
    def press(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        fail_after_loss: bool = False,
    ) -> Iterator[str]:
        self.seen.append(cancel)
        yield "working"
        if fail_after_loss:
            self.held.lost.set()
            raise InstallerError("the press stopped")
        yield "done"


def test_a_press_is_stopped_when_its_reservation_is_lost(tmp_path: Path) -> None:
    """The press's OWN cancel is set, so the job runner, `withdraw_stop()` and "Stop now anyway"
    (which read that very object) see it as they see a Stop.

    Mutation this catches: the loss not reaching the cancel (the press goes on to its end).
    """
    held = docker.ClaimHeld("yulon-busy-x", threading.Event())
    toy = _Toy(held)
    stop = threading.Event()
    lines = toy.press(InstallOptions(server_dir=tmp_path), cancel=stop)
    assert next(lines) == "working"
    (cancel,) = toy.seen
    assert cancel is stop and not stop.is_set()
    held.lost.set()
    deadline = time.monotonic() + HANG_BOUND
    while not stop.is_set():
        assert time.monotonic() < deadline, "the press does not see its reservation lost"
        time.sleep(0.02)
    lines.close()


def test_a_press_with_no_cancel_is_given_one_the_loss_sets(tmp_path: Path) -> None:
    held = docker.ClaimHeld("yulon-busy-x", threading.Event())
    toy = _Toy(held)
    lines = toy.press(InstallOptions(server_dir=tmp_path))
    next(lines)
    (cancel,) = toy.seen
    assert isinstance(cancel, threading.Event) and not cancel.is_set()
    held.lost.set()
    assert cancel.wait(HANG_BOUND)
    lines.close()


def test_the_loss_watcher_ends_with_its_press(tmp_path: Path) -> None:
    before = {t for t in threading.enumerate() if t.name == "yulon-reservation-loss"}
    toy = _Toy(docker.ClaimHeld("yulon-busy-x", threading.Event()))
    list(toy.press(InstallOptions(server_dir=tmp_path)))
    deadline = time.monotonic() + HANG_BOUND
    while {t for t in threading.enumerate() if t.name == "yulon-reservation-loss"} - before:
        assert time.monotonic() < deadline, "the watcher outlived its press"
        time.sleep(0.05)


def test_a_press_stopped_by_a_loss_says_what_happened_and_what_to_press(tmp_path: Path) -> None:
    toy = _Toy(docker.ClaimHeld("yulon-busy-x", threading.Event()))
    out: list[str] = []
    with pytest.raises(InstallerError, match="the press stopped"):
        for line in toy.press(InstallOptions(server_dir=tmp_path), fail_after_loss=True):
            out.append(line)
    assert out[0] == "working"
    said = out[-1]
    assert "A toy press" in said and "ended from elsewhere" in said, said
    assert "started nothing" in said and "Press Start, or \u201cRebuild" in said, said


def test_a_press_that_failed_for_another_reason_adds_nothing(tmp_path: Path) -> None:
    held = docker.ClaimHeld("yulon-busy-x", threading.Event())
    toy = _Toy(held)
    out: list[str] = []

    @native._reserving("A toy press")
    def failing(self: _Toy, options: Any = None, *, cancel: Any = None) -> Iterator[str]:
        yield "working"
        raise InstallerError("a plain failure")

    with pytest.raises(InstallerError, match="a plain failure"):
        for line in failing(toy, InstallOptions(server_dir=tmp_path)):
            out.append(line)
    assert out == ["working"]


def test_sql_stops_when_the_hold_is_lost_between_statements(tmp_path: Path) -> None:
    """Mutation this catches: no loss check, so every statement went on after the Stop."""
    lost = threading.Event()
    spy = _Spy()

    @contextmanager
    def hold(press: str) -> Iterator[docker.ClaimHeld]:
        yield docker.ClaimHeld("yulon-busy-x", lost)

    applier, sql = _stackables(tmp_path, spy, hold=hold)
    real = sql.run_statement

    def first_statement_then_the_loss(db: str, statement: str) -> None:
        real(db, statement)
        lost.set()  # another Yu'lon's Stop anyway removed the reservation

    sql.run_statement = first_statement_then_the_loss  # type: ignore[method-assign]
    with pytest.raises(ApplyRefusal) as refused:
        applier.install(parse_manifest(STACKABLES))
    assert len(sql.statements) == 1 and sql.files == [], "SQL went on after the hold was lost"
    assert "stopped" in str(refused.value) and "no more SQL was sent" in str(refused.value)


def test_a_relative_sql_text_is_not_sent_once_the_hold_is_lost(tmp_path: Path) -> None:
    """Codex review: a mob mod's one relative statement returns before the loop's check, and
    it marks its record pending first. Neither happens under a lost hold.

    Mutation this catches: the check missing from `_run_relative()`.
    """
    from tests.test_mob_multiplier_rerun import _all, _Db, _mob
    from yulon import module_answers

    lost = threading.Event()
    lost.set()

    @contextmanager
    def hold(press: str) -> Iterator[docker.ClaimHeld]:
        yield docker.ClaimHeld("yulon-busy-x", lost)

    db = _Db()
    applier = Applier(tmp_path, sql=db, world_running=lambda: False, hold_server=hold)
    with pytest.raises(ApplyRefusal) as refused:
        applier.install(_mob(), _all("2"))
    assert "no more SQL was sent" in str(refused.value)
    assert db.texts == [], "a relative statement went out under a lost hold"
    assert not (tmp_path / module_answers.ANSWERS_FILE).exists(), "the record was marked pending"


def test_an_install_whose_reservation_is_lost_stops_before_the_import(tmp_path: Path) -> None:
    """Codex review: the install discarded its claim, so a lost reservation changed nothing.

    Mutation this catches: the install's context cancel not reading the reservation's loss.
    """
    from tests.support_native import install

    rec = Recorder()
    lost = threading.Event()

    @contextmanager
    def claim(server_dir: Path, *, press: str, **_kw: Any) -> Iterator[docker.ClaimHeld]:
        lost.set()  # another Yu'lon's Stop anyway took it at once
        yield docker.ClaimHeld("yulon-busy-x", lost)

    with pytest.raises(InstallerError):
        install(rec, tmp_path / "wow", server_claim=claim)
    assert "one-shot:ac-db-import" not in rec.calls, rec.calls


def test_a_reservation_already_lost_when_the_press_starts_stops_it_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not left to a watcher thread: the press's first check must already see the Stop.

    The watcher is made unable to run, so only the entry check can answer.
    Mutation this catches: the entry check dropped (the install test above then passes or
    fails by how fast its stages run).
    """

    class NoThread:
        def __init__(self, *_a: Any, **_kw: Any) -> None: ...

        def start(self) -> None: ...

    monkeypatch.setattr(native.threading, "Thread", NoThread)
    lost = threading.Event()
    lost.set()
    cancel = native._end_on_loss(docker.ClaimHeld("yulon-busy-x", lost), None, threading.Event())
    assert cancel.is_set()


def test_sql_runs_unreserved_when_the_reservation_is_moot_and_is_refused_when_it_is_not(
    tmp_path: Path,
) -> None:
    """Like every other press: Docker not answering is met by the SQL in its own words.

    Mutation this catches: every `ServerReservationUnavailable` refused (cold review note).
    """

    def unavailable(moot: bool) -> Any:
        @contextmanager
        def hold(press: str) -> Iterator[None]:
            raise docker.ServerReservationUnavailable("Docker is not answering.", moot=moot)
            yield

        return hold

    spy = _Spy()
    applier, sql = _stackables(tmp_path / "a", spy, hold=unavailable(True))
    applier.install(parse_manifest(STACKABLES))
    assert sql.files, "a moot reservation refused the SQL"

    applier, sql = _stackables(tmp_path / "b", _Spy(), hold=unavailable(False))
    with pytest.raises(ApplyRefusal, match="Docker is not answering"):
        applier.install(parse_manifest(STACKABLES))
    assert sql.files == [] and sql.statements == []


@pytest.mark.parametrize(
    ("method", "press"),
    [
        ("remove_kept_build", native.REMOVE_KEPT_BUILD_LABEL),
        ("repair_base_compose", native.REPAIR_FILES_LABEL),
    ],
)
def test_the_one_call_presses_reserve_too_and_are_refused_before_they_touch_anything(
    tmp_path: Path, method: str, press: str
) -> None:
    """ "Remove kept build" deletes the `-parked` images another process's rollback needs;
    "Repair server files" rewrites the compose file. Opus adversarial review.

    Mutation this catches: either left unreserved."""
    taken: list[str] = []
    rec = Recorder()
    installer = engine(rec, server_claim=_refusing(taken))
    with pytest.raises(InstallerError, match="Another Yu'lon is working on WoW"):
        getattr(installer, method)(InstallOptions(server_dir=_installed(tmp_path)))
    assert taken == [press]
    assert rec.calls == []
