"""T623: the movement-map job takes the server's cross-process hold only where it writes.

The job runs for hours, so it does not hold the server for its run. It holds for the moments that
write: the Server tab's Start and Stop, and the status poll when it has a transition to record
(a run that ended, vanished, or a finished set that is no longer whole). Every press that replaces
or removes `data/mmaps` (Rebuild, Update, Return to the pin, Re-extract, Uninstall) already
reserves the server and stops the job first, from the record. Tests drive `mmaps` with
`FakeMmapsDocker` and the assembly with the fake docker CLI and another process holding.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from tests.support_trinitycore import FakeMmapsDocker
from tests.test_families_trinitycore import (  # noqa: F401 - fixtures, as pytest resolves them
    ENTRY,
    Machine,
    engine,
    install,
    known_password,
    machine,
)
from tests.test_mmaps_background import (  # noqa: F401 - fixtures and helpers of the job's tests
    INSTALL_ID,
    MIN_FILES,
    NAME,
    Clock,
    conf_text,
    lay_server,
    output,
    record,
    start,
    status,
)
from tests.test_more_writes_hold import HELD, _Nothing
from yulon import docker, platform
from yulon.catalog.families import mmaps
from yulon.ui import controller_view as controller_view_module

PUT = "Record the pathfinding data's result"


@pytest.fixture
def server(tmp_path: Path) -> Path:
    server_dir = tmp_path / "wow-centurion-server"
    lay_server(server_dir)
    return server_dir


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text("yulon.local/wotlk-server:native\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


def _assembled(server_dir: Path) -> controller_view_module.ControllerServices:
    return controller_view_module._assemble(
        ENTRY,
        server_dir,
        client_dir=None,
        wsl_distro=None,
        controller=_Nothing(),  # type: ignore[arg-type]
        sql=_Nothing(),  # type: ignore[arg-type]
        send_console=lambda _c: None,  # type: ignore[arg-type,return-value]
        create_account=lambda name, pw, level: None,  # type: ignore[arg-type,return-value]
        store=None,
        applier=None,
        backup=lambda: None,  # type: ignore[arg-type,return-value]
        plan_restore=lambda *_a: None,  # type: ignore[arg-type,return-value]
        restore=lambda _p: None,  # type: ignore[arg-type,return-value]
    )


def _held_by_another_yulon(fake_docker: Path, server_dir: Path) -> Any:
    from tests.test_server_reservation import _another_yulon_holds

    return _another_yulon_holds(
        fake_docker, docker.SERVER_CLAIM_PREFIX + str(docker.folder_id(server_dir))
    )


def _state(server_dir: Path) -> tuple[str | None, bool]:
    """The record's state (None: no record) and whether worldserver.conf has pathfinding on."""
    try:
        said: str | None = str(record(server_dir)["state"])
    except FileNotFoundError:
        said = None
    return said, "mmap.enablePathFinding = 1" in conf_text(server_dir)


class Probe:
    """A hold that notes what the server's files said when it was taken and let go.

    `refuse` makes it the refusal of a server another Yu'lon holds; `meanwhile` is what the
    other Yu'lon does while this one waited for it.
    """

    def __init__(
        self,
        server_dir: Path,
        *,
        refuse: Exception | None = None,
        meanwhile: Callable[[], None] | None = None,
    ) -> None:
        self.server_dir = server_dir
        self.refuse = refuse
        self.meanwhile = meanwhile
        self.taken: list[tuple[str, float | None]] = []
        self.at_take: tuple[str | None, bool] | None = None
        self.at_release: tuple[str | None, bool] | None = None

    @contextmanager
    def __call__(self, press: str, *, budget: float | None = None) -> Iterator[None]:
        self.taken.append((press, budget))
        if self.refuse is not None:
            raise self.refuse
        self.at_take = _state(self.server_dir)
        if self.meanwhile is not None:
            self.meanwhile()
        yield
        self.at_release = _state(self.server_dir)


def _refusal() -> docker.ServerReserved:
    return docker.ServerReserved(HELD, docker.ServerHolder("yulon-busy-x", "id"))


def poll(server_dir: Path, fake: FakeMmapsDocker, hold: Any, clock: Clock | None = None) -> Any:
    return mmaps.mmaps_status(
        server_dir, ENTRY, runner=fake, clock=clock or Clock(), install_id=INSTALL_ID, hold=hold
    )


def _ended_run(server_dir: Path, fake: FakeMmapsDocker, clock: Clock) -> None:
    """A run that finished its tiles while nobody asked."""
    start(server_dir, fake, clock)
    clock.now = datetime(2026, 10, 2, 15, 0, 0, tzinfo=UTC)
    fake.finish(0, tiles=MIN_FILES)


# ------------------------------------------------------------------ the poll's transitions


def test_a_poll_that_records_a_finished_run_does_it_inside_the_hold(server: Path) -> None:
    """Mutation: write the done record and the conf switch before taking the hold."""
    fake, clock = FakeMmapsDocker(), Clock()
    _ended_run(server, fake, clock)
    probe = Probe(server)
    now = poll(server, fake, probe, clock)
    assert now.state == "done" and now.pathfinding_on
    assert [press for press, _budget in probe.taken] == [PUT]
    assert probe.at_take == ("running", False), "nothing written before the hold was taken"
    assert probe.at_release == ("done", True)


def test_the_polls_hold_is_bounded_so_a_poll_never_waits_a_presss_full_take(
    server: Path,
) -> None:
    """Mutation: take the poll's hold without a budget."""
    fake, clock = FakeMmapsDocker(), Clock()
    _ended_run(server, fake, clock)
    probe = Probe(server)
    poll(server, fake, probe, clock)
    assert probe.taken == [(PUT, docker.GUI_HOLD_BUDGET_SECONDS)]


def test_a_poll_whose_hold_is_refused_writes_nothing_and_says_what_it_read(
    server: Path,
) -> None:
    """Another Yu'lon holds: the run reads as running, the conf stays off, the container stays.

    Mutation: swallow the refusal and write anyway, or let it out of the poll."""
    fake, clock = FakeMmapsDocker(), Clock()
    _ended_run(server, fake, clock)
    refused = Probe(server, refuse=_refusal())
    now = poll(server, fake, refused, clock)
    assert now.state == "running"
    assert _state(server) == ("running", False)
    assert NAME in fake.jobs, "the finished container is removed only once it is recorded"
    assert len(output(server)) == MIN_FILES
    free = Probe(server)
    assert poll(server, fake, free, clock).state == "done", "the next poll records it"
    assert _state(server) == ("done", True)


def test_a_poll_whose_hold_cannot_be_made_at_all_writes_nothing_either(server: Path) -> None:
    """`ServerReservationUnavailable` (no image to reserve from) is not a reason to write unheld."""
    fake, clock = FakeMmapsDocker(), Clock()
    _ended_run(server, fake, clock)
    unavailable = Probe(
        server, refuse=docker.ServerReservationUnavailable("No image to reserve from.")
    )
    assert poll(server, fake, unavailable, clock).state == "running"
    assert _state(server) == ("running", False)


def test_a_poll_reads_the_record_again_inside_the_hold(server: Path) -> None:
    """Another Yu'lon's poll recorded the end while this one waited: that is what is read.

    Mutation: carry the first read's facts into the hold and record over theirs (the container
    they removed then reads as a lost one, and the finished set as a failed run)."""
    fake = FakeMmapsDocker()
    clock, theirs_clock = Clock(), Clock()
    _ended_run(server, fake, clock)
    theirs_clock.now = datetime(2026, 10, 2, 15, 30, 0, tzinfo=UTC)
    clock.now = datetime(2026, 10, 2, 18, 0, 0, tzinfo=UTC)

    def theirs() -> None:
        assert status(server, fake, theirs_clock).state == "done"

    probe = Probe(server, meanwhile=theirs)
    now = poll(server, fake, probe, clock)
    assert now.state == "done" and now.pathfinding_on
    assert _state(server) == ("done", True)
    saved = record(server)
    assert saved["finished"] == "2026-10-02T15:30:00.000000Z", "their record, not rewritten"
    assert saved["pathfinding_on_at"] == "2026-10-02T15:30:00.000000Z"


def test_a_run_that_vanished_is_recorded_inside_the_hold_or_not_at_all(server: Path) -> None:
    """A lost container trims the cut tile and writes `failed`: a write, so a held one."""
    fake, clock = FakeMmapsDocker(), Clock()
    start(server, fake, clock)
    fake.write_tiles(3)
    fake.write_cut_tile()
    fake.vanish()
    refused = Probe(server, refuse=_refusal())
    assert poll(server, fake, refused, clock).state == "running"
    assert "0013251.mmtile" in output(server), "the cut tile is still there: nothing was trimmed"
    assert _state(server)[0] == "running"
    probe = Probe(server)
    assert poll(server, fake, probe, clock).state == "failed"
    assert probe.at_take == ("running", False)
    assert "0013251.mmtile" not in output(server)


def test_a_done_set_that_is_no_longer_whole_is_cleared_inside_the_hold(server: Path) -> None:
    """The set emptied by hand or by a new extraction: cleared and forgotten, a held write."""
    fake, clock = FakeMmapsDocker(), Clock()
    _ended_run(server, fake, clock)
    assert poll(server, fake, None, clock).state == "done"
    for tile in sorted((server / "data" / "mmaps").iterdir())[5:]:
        tile.unlink()
    refused = Probe(server, refuse=_refusal())
    assert poll(server, fake, refused, clock).state == "done"
    assert _state(server) == ("done", True), "not cleared while another Yu'lon holds"
    probe = Probe(server)
    assert poll(server, fake, probe, clock).state == "not-started"
    assert probe.at_take == ("done", True)
    assert probe.at_release == (None, False)


def test_a_finished_run_whose_switch_is_still_owed_takes_the_hold_for_the_switch(
    server: Path,
) -> None:
    """The done record is there but the conf switch failed earlier: the retry is a conf write."""
    fake, clock = FakeMmapsDocker(), Clock()
    _ended_run(server, fake, clock)
    assert poll(server, fake, None, clock).state == "done"
    from yulon.catalog.families import conf

    conf.set_keys(server / "etc" / "worldserver.conf", {mmaps.PATHFINDING_KEY: "0"})
    # The record says it was switched on; take that back, as a failed switch leaves it.
    saved = mmaps.read_record(server)
    assert saved is not None
    mmaps._write_record(server, dataclasses.replace(saved, pathfinding_on_at=""))
    refused = Probe(server, refuse=_refusal())
    poll(server, fake, refused, clock)
    assert _state(server) == ("done", False)
    probe = Probe(server)
    assert poll(server, fake, probe, clock).state == "done"
    assert _state(server) == ("done", True)
    assert [press for press, _b in probe.taken] == [PUT]


def test_a_poll_with_nothing_to_record_takes_no_hold(server: Path) -> None:
    """A run still running, a record that is up to date, no record: the poll is a read.

    Mutation: take the hold at the top of `mmaps_status`."""
    fake, clock = FakeMmapsDocker(), Clock()
    probe = Probe(server)
    assert poll(server, fake, probe, clock).state == "not-started"
    start(server, fake, clock)
    fake.say("12% [Map 000] Building tile [01,02]")
    assert poll(server, fake, probe, clock).percent == 12  # a progress write: unheld, see T623
    fake.finish(0, tiles=MIN_FILES)
    assert poll(server, fake, None, clock).state == "done"
    assert poll(server, fake, probe, clock).state == "done", "done, switched on: a read"
    assert probe.taken == []


def test_a_caller_with_no_hold_records_inline_as_before(server: Path) -> None:
    """The engine's own calls run inside a press that holds already: they pass no hold."""
    fake, clock = FakeMmapsDocker(), Clock()
    _ended_run(server, fake, clock)
    assert status(server, fake, clock).state == "done"
    assert _state(server) == ("done", True)


# ------------------------------------------------------------------ the Server tab's presses


def test_the_assembled_start_is_refused_while_another_yulon_holds_the_server(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: pass `start_mmaps` through unwrapped in `_pathfinding`."""
    started: list[object] = []
    monkeypatch.setattr(mmaps, "start_mmaps", lambda *_a, **_k: started.append(1) or "started")
    seam = _assembled(server).pathfinding
    assert seam is not None
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            seam.start()
        assert mmaps.START_PRESS in str(refused.value)
    finally:
        theirs.kill()
    assert started == []


def test_the_assembled_start_and_stop_run_inside_the_hold_when_nobody_holds(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held: list[tuple[str, bool]] = []

    def took(name: str) -> Callable[..., str]:
        return lambda *_a, **_k: held.append((name, bool(docker._RESERVATIONS))) or name

    monkeypatch.setattr(mmaps, "start_mmaps", took("start"))
    monkeypatch.setattr(mmaps, "stop_mmaps", took("stop"))
    seam = _assembled(server).pathfinding
    assert seam is not None
    assert (seam.start(), seam.stop()) == ("start", "stop")
    assert held == [("start", True), ("stop", True)]
    assert docker._RESERVATIONS == {}, "and let go"


def test_the_assembled_stop_is_refused_while_another_yulon_holds_the_server(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: pass `stop_mmaps` through unwrapped in `_pathfinding`."""
    stopped: list[object] = []
    monkeypatch.setattr(mmaps, "stop_mmaps", lambda *_a, **_k: stopped.append(1) or "stopped")
    seam = _assembled(server).pathfinding
    assert seam is not None
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        with pytest.raises(docker.ServerReserved, match="Stop making the pathfinding data"):
            seam.stop()
    finally:
        theirs.kill()
    assert stopped == []


def test_the_assembled_poll_is_given_a_bounded_hold_for_its_transitions(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: do not hand `hold=` to `mmaps_status` in `_pathfinding`."""
    given: dict[str, Any] = {}

    def fake_status(_dir: Path, _entry: object, **kwargs: Any) -> mmaps.MmapsStatus:
        given.update(kwargs)
        return mmaps.MmapsStatus("not-started")

    monkeypatch.setattr(mmaps, "mmaps_status", fake_status)
    seam = _assembled(server).pathfinding
    assert seam is not None
    seam.status()
    hold = given["hold"]
    theirs = _held_by_another_yulon(fake_docker, server)
    try:
        with pytest.raises(docker.ServerReserved, match=PUT):
            with hold(PUT):
                pass
    finally:
        theirs.kill()
    budgets: list[float | None] = []
    monkeypatch.setattr(
        docker,
        "server_hold",
        lambda *_a, budget=None, **_k: budgets.append(budget) or Probe(server)("x"),
    )
    with hold(PUT):
        pass
    assert budgets == [docker.GUI_HOLD_BUDGET_SECONDS]


# ------------------------------------------------------------------ the view


def test_stop_is_not_offered_while_a_press_of_this_app_runs(qapp: object, tmp_path: Path) -> None:
    """The in-process hold is shared, so the view says wait (Start already did).

    Mutation: leave `_busy` out of the Stop button's enabled test and out of `stop_pathfinding`."""
    from dataclasses import replace

    from tests.test_centurion_view import _Pathfinding, _seam, _server
    from yulon.ui.controller_view import ControllerServices, ControllerView
    from yulon.ui.widgets.job import run_inline

    fake = _Pathfinding(mmaps.MmapsStatus(state="running", percent=37))
    services = replace(
        ControllerServices.for_entry(ENTRY, _server(tmp_path)), pathfinding=_seam(fake)
    )
    view = ControllerView(ENTRY, services, status_poll_ms=0, job_runner=run_inline)
    view.refresh_pathfinding()
    assert view.pathfinding_stop_button.isEnabled()
    view._set_busy(True)
    assert not view.pathfinding_stop_button.isEnabled()
    view.stop_pathfinding()
    assert fake.stopped == 0, "Stop ran beside a press of this app"
    view._set_busy(False)
    assert view.pathfinding_stop_button.isEnabled()
    view.stop_pathfinding()
    assert fake.stopped == 1


# ------------------------------------------------------------------ the writers that stay lower


WRITERS = {
    "start_mmaps": ("held", "the Server tab's Start: `_pathfinding` wraps it in the hold"),
    "stop_mmaps": ("held", "the Server tab's Stop: `_pathfinding` wraps it in the hold"),
    "mmaps_status": ("held", "its transitions take the hold it is given (`hold=`)"),
    "stop_for_route": ("lower", "Rebuild/Update/Return/Re-extract: engine presses reserve"),
    "discard": ("lower", "Re-extract: `reextract` is an engine press that reserves"),
    "remove_for_uninstall": ("lower", "Uninstall: `Uninstaller.run` holds for the whole removal"),
    "overlay": ("reads", "a conf table"),
    "background_block": ("reads", "a catalog fact"),
    "continues_from": ("reads", "a count"),
    "container_name": ("reads", "a name"),
    "job_for": ("reads", "a value"),
    "image_ref": ("reads", "a reference"),
    "read_record": ("reads", "the record"),
}


def test_every_public_function_of_the_job_says_what_it_writes() -> None:
    """Mutation: add a public function to `mmaps` and leave it out of `WRITERS`."""
    public = {
        name
        for name, member in inspect.getmembers(mmaps, inspect.isfunction)
        if not name.startswith("_") and member.__module__ == mmaps.__name__
    }
    assert public - set(WRITERS) == set(), f"{sorted(public - set(WRITERS))}: reads, or holds?"
    assert set(WRITERS) - public == set(), f"{sorted(set(WRITERS) - public)}: gone"
    assert {kind for kind, _why in WRITERS.values()} <= {"held", "lower", "reads"}


def test_a_press_that_replaces_the_maps_stops_the_job_from_the_record_alone(
    server: Path,
) -> None:
    """Another Yu'lon's Rebuild finds this one's running job by its record, and stops it first.

    A fresh runner and no shared memory: the record and the container's name are the state."""
    fake, clock = FakeMmapsDocker(), Clock()
    start(server, fake, clock)
    fake.write_tiles(4)
    said = mmaps.stop_for_route(
        server,
        ENTRY,
        "the rebuild",
        clear=False,
        runner=fake,
        install_id=INSTALL_ID,
    )
    assert said is not None and "Stopped making the pathfinding data" in said
    assert fake.jobs == {}, "the container is gone before the press changes anything"
    assert _state(server) == ("failed", False), "and pathfinding stays off"
    assert len(output(server)) == 4, "its finished tiles are kept for the run after the rebuild"


def test_the_presses_that_reserve_are_the_ones_that_stop_the_job() -> None:
    """The `lower` rows. Mutation: take `@_reserving` off `reextract` or `rebuild`."""
    from yulon.catalog.families.trinitycore import TrinityCoreInstaller
    from yulon.catalog.native import StagedInstaller

    for owner, name in (
        (TrinityCoreInstaller, "reextract"),
        (StagedInstaller, "rebuild"),
        (StagedInstaller, "update_to_latest"),
    ):
        assert hasattr(getattr(owner, name), "__wrapped__"), f"{name} is unreserved"


def test_an_uninstall_stops_the_job_inside_the_hold(tmp_path: Path) -> None:
    """Mutation: move `_stop_background_jobs()` above the hold's `with` in `Uninstaller.run`."""
    from tests.test_more_writes_hold import _Hold
    from tests.test_purge import _recorder
    from yulon import purge

    rec = _recorder(tmp_path)
    hold = _Hold(rec.order)
    rec.uninstaller(
        hold_server=hold, stop_background_jobs=lambda: rec.order.append("stop_background")
    ).run(keep_characters=False)
    taken = rec.order.index(f"hold:{purge.UNINSTALL_PRESS}")
    assert taken < rec.order.index("stop_background") < rec.order.index("release")


def test_the_table_of_services_names_tests_that_exist() -> None:
    """The `pathfinding` row of `test_last_writes_hold.SEAMS` is held, by a test in this file."""
    from tests import test_last_writes_hold as table

    kind, where = table.SEAMS["pathfinding"]
    assert kind == "held"
    module, _, test = where.rpartition(".")
    assert module == __name__ and callable(getattr(importlib.import_module(module), test, None))
