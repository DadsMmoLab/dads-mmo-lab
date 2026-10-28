"""Ordered start: auth after the world said ready, then the realm opens (T812 C6).

Conquest of Azeroth's authserver lists the realm as online the moment its
row's flag says so, so auth goes up only once the world is listening and the
row's offline bit is then cleared. One function (`ordered_start.start`) serves all
three start paths: the install's `up`, the rebuild/update's `recreate`, and
`Controller.start`. The flag off is today's single `up`, so WotLK is the
regression each test below keeps.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from yulon import docker
from yulon.catalog import native, ordered_start
from yulon.catalog.catalog import load_catalog


def _entry(entry_id: str):
    entry = next(game for game in load_catalog().games if game.id == entry_id)
    return entry


COA = _entry("wow-coa")
WOTLK = _entry("wow-wotlk")


def _spec(entry) -> docker.ContainerSpec:
    return entry.container_spec()


def _ready(entry) -> docker.ReadySpec:
    return native.ready_spec_for(entry, entry.install.native.ready)


class _Calls:
    """The seams `ordered_start.start` is driven through, recording the order."""

    def __init__(self, *, world_ready: bool = True, auth_ready: bool = True) -> None:
        self.events: list[str] = []
        self.world_ready = world_ready
        self.auth_ready = auth_ready
        self.said: list[str] = []
        self.opened = 0

    def start_services(self, spec, server_dir, **kwargs):
        self.events.append(f"up {sorted(kwargs.get('services') or [])}")
        return True

    def wait_world(self, spec, ready, **kwargs):
        assert ready.auth is None, "the world wait must not wait on a stopped auth"
        self.events.append("wait world")
        return self.world_ready

    def wait_auth(self, spec, ready, **kwargs):
        assert ready.world is not None and ready.auth is not None, (
            "the auth wait re-confirms the world banner off the world log and reads the "
            "auth line off the auth log; a world pattern of the auth line can never match"
        )
        self.events.append("wait auth")
        return self.auth_ready

    def open_realm(self):
        self.opened += 1
        self.events.append("open realm")
        return None


def _drive(calls: _Calls, entry=COA, **overrides):
    spec = _spec(entry)
    kwargs = {
        "entry": entry,
        "start_services": calls.start_services,
        "wait_world": calls.wait_world,
        "wait_auth": calls.wait_auth,
        "open_realm": calls.open_realm,
    }
    kwargs.update(overrides)
    return ordered_start.start(spec, Path("/tmp/coa"), **kwargs)


def test_auth_starts_only_after_the_world_said_ready(monkeypatch) -> None:
    """The call order is the feature: db+world, world wait, auth, auth wait, open."""
    calls = _Calls()
    monkeypatch.setattr(docker, "status", lambda **kw: ["coa-database", "coa-worldserver"])
    assert _drive(calls) is True
    spec = _spec(COA)
    db, world = spec.service_for(spec.db), spec.service_for(spec.world)
    auth = spec.service_for(spec.auth)
    assert calls.events[0] == f"up {sorted([db, world])}", calls.events
    assert auth not in calls.events[0]
    assert calls.events[1:] == ["wait world", f"up {sorted([auth])}", "wait auth", "open realm"]
    # The names are compose services, and the subset still selects: dropping
    # `services=` anywhere between here and `compose up` reddens this.
    assert docker.staged_up_argv(spec, services=sorted([db, world]))[4:] == sorted([db, world])


def test_a_world_that_never_gets_ready_never_starts_auth(monkeypatch) -> None:
    """No world banner, no auth up: the refusal names the world and its log."""
    calls = _Calls(world_ready=False)
    monkeypatch.setattr(docker, "status", lambda **kw: [])
    with pytest.raises(docker.DockerRefusal, match="never said it was ready"):
        _drive(calls)
    assert [event for event in calls.events if event.startswith("up")] == [calls.events[0]]
    assert "wait auth" not in calls.events and calls.opened == 0


def test_the_realm_is_opened_after_auth_said_ready(monkeypatch) -> None:
    """`open_realm` runs after the auth wait, and it clears only the offline bit."""
    from yulon import realm_flag

    calls = _Calls()
    monkeypatch.setattr(
        docker, "status", lambda **kw: ["coa-database", "coa-worldserver", "coa-authserver"]
    )
    assert _drive(calls) is True
    assert calls.events[-1] == "open realm"
    assert (
        realm_flag.open_statement(COA)
        == "UPDATE acore_auth.realmlist SET flag = flag & ~2 WHERE id=1;"
    )


def test_a_failed_open_is_said_and_the_start_still_counts(monkeypatch, caplog) -> None:
    """The Keeper and the uptime tick are the backstop: a failed open never fails the start."""
    calls = _Calls()
    monkeypatch.setattr(
        docker, "status", lambda **kw: ["coa-database", "coa-worldserver", "coa-authserver"]
    )

    def refuse():
        calls.events.append("open realm")
        return "wow-coa: the realm could not be listed Online"

    with caplog.at_level("INFO", logger="yulon.catalog.ordered_start"):
        assert _drive(calls, open_realm=refuse) is True
    assert calls.events[-1] == "open realm"
    assert "could not be listed Online" in caplog.text


def test_without_the_flag_one_up_names_all_three() -> None:
    """WotLK regression: flag off is exactly today's single `up`."""
    seen: list[object] = []

    def start_all(spec, server_dir, **kwargs):
        seen.append(kwargs)
        return True

    spec = _spec(WOTLK)
    assert (
        ordered_start.start(
            spec,
            Path("/tmp/wotlk"),
            entry=WOTLK,
            start_services=start_all,
            wait_world=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no wait")),
            wait_auth=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no wait")),
            open_realm=lambda: (_ for _ in ()).throw(AssertionError("no open")),
        )
        is True
    )
    assert seen == [{"wsl_distro": None, "force_recreate": False}], seen


def test_staged_up_argv_names_only_the_asked_services() -> None:
    """`services=` selects the subset; None stays all three; `--no-deps` stays either way."""
    spec = _spec(COA)
    assert docker.staged_up_argv(spec) == [
        "compose",
        "up",
        "-d",
        "--no-deps",
        *spec.compose_services(),
    ]
    subset = [spec.service_for(spec.db), spec.service_for(spec.world)]
    assert docker.staged_up_argv(spec, services=subset) == [
        "compose",
        "up",
        "-d",
        "--no-deps",
        *subset,
    ]
    assert docker.recreate_argv(spec, services=subset) == [
        "compose",
        "up",
        "-d",
        "--force-recreate",
        "--no-deps",
        *subset,
    ]


def test_controller_start_orders_a_coa_server(monkeypatch, tmp_path) -> None:
    """`Controller.start` on a CoA spec goes through the ordered start."""
    from yulon import realm_flag
    from yulon.controller import Controller

    ups: list[list[str]] = []
    monkeypatch.setattr(
        docker,
        "start_staged",
        lambda spec, server_dir, **kw: ups.append(sorted(kw.get("services") or [])) or True,
    )
    monkeypatch.setattr(native, "wait_ready_quietly", lambda *a, **k: True)
    monkeypatch.setattr(
        docker, "status", lambda **kw: ["coa-database", "coa-worldserver", "coa-authserver"]
    )
    monkeypatch.setattr(realm_flag, "open_realm", lambda *a, **k: None)
    marked: list[str] = []
    monkeypatch.setattr(
        realm_flag, "mark_offline", lambda *a, **k: marked.append("marked") or False
    )
    ctl = Controller(_spec(COA), tmp_path)
    ctl.entry = COA
    ctl._start_servers()
    spec = _spec(COA)
    db, world = spec.service_for(spec.db), spec.service_for(spec.world)
    # Auth already running is left as it is: one up, of db+world only.
    assert ups == [sorted([db, world])], ups
    assert marked == ["marked"], marked
    # Auth down is brought up after the world wait: two ups, in order.
    ups.clear()
    monkeypatch.setattr(docker, "status", lambda **kw: [])
    ctl._start_servers()
    auth = spec.service_for(spec.auth)
    assert ups == [sorted([db, world]), [auth]], ups


def test_rebuild_recreate_orders_a_coa_server(monkeypatch) -> None:
    """The rebuild's recreate goes through the ordered replace for a CoA entry."""
    from yulon import realm_flag
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller
    from yulon.catalog.native import Seams

    monkeypatch.setattr(realm_flag, "open_realm", lambda *a, **k: None)

    events: list[str] = []

    def recreate(spec, server_dir, **kwargs):
        events.append(f"recreate {sorted(kwargs.get('services') or [])}")
        return True

    def wait_ready(spec, ready):
        events.append("wait")
        return True

    installer = AzerothCoreInstaller(
        COA,
        seams=Seams(
            recreate=recreate,  # type: ignore[arg-type]
            wait_ready=wait_ready,  # type: ignore[arg-type]
        ),
    )
    control = docker.StopControl()
    signalled: list[str] = []
    assert (
        installer._recreate_ordered(
            _spec(COA),
            Path("/tmp/coa"),
            control=control,
            before_signal=lambda: signalled.append("touched"),
        )
        is True
    )
    assert [event.split()[0] for event in events] == ["recreate", "wait", "recreate", "wait"]
    assert signalled == ["touched"], "the touched boundary fires before the first replace"


def test_a_stop_during_the_recreate_wait_lets_the_load_finish_then_stops(monkeypatch) -> None:
    """A rebuild never kills a loading world: Stop waits it out, then still stops."""
    from yulon import realm_flag
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller
    from yulon.catalog.installer import ReadyWaitStopped
    from yulon.catalog.native import Seams

    monkeypatch.setattr(realm_flag, "open_realm", lambda *a, **k: None)
    waits = 0

    def wait_ready(spec, ready):
        nonlocal waits
        waits += 1
        return waits > 1

    control = docker.StopControl()
    control.abandon.set()
    installer = AzerothCoreInstaller(
        COA,
        seams=Seams(
            recreate=lambda *a, **k: True,  # type: ignore[arg-type]
            wait_ready=wait_ready,  # type: ignore[arg-type]
        ),
    )
    with pytest.raises(ReadyWaitStopped, match="Stop was pressed"):
        installer._recreate_ordered(_spec(COA), Path("/tmp/coa"), control=control)
    assert waits == 2, "the stopped wait ran again uncancelled before stopping"


def test_a_stop_during_the_world_wait_is_a_stop_not_a_failure(monkeypatch) -> None:
    """Cancel ending the world wait raises `ReadyWaitStopped`, never NEVER_READY."""
    from yulon.catalog.installer import ReadyWaitStopped

    calls = _Calls(world_ready=False)
    monkeypatch.setattr(docker, "status", lambda **kw: [])
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ReadyWaitStopped, match="Stop was pressed"):
        _drive(calls, cancel=cancel)


def test_a_stop_during_the_auth_wait_is_a_stop_not_a_failure(monkeypatch) -> None:
    """Cancel ending the auth wait raises `ReadyWaitStopped`, never AUTH_NEVER_READY."""
    from yulon.catalog.installer import ReadyWaitStopped

    calls = _Calls(auth_ready=False)
    monkeypatch.setattr(
        docker, "status", lambda **kw: ["coa-database", "coa-worldserver", "coa-authserver"]
    )
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ReadyWaitStopped, match="Stop was pressed"):
        _drive(calls, cancel=cancel)


def test_an_auth_only_recreate_stop_leaves_the_world_alone() -> None:
    """The ordered recreate's second stop names auth only: the readied world keeps running."""
    spec = _spec(COA)
    auth = spec.service_for(spec.auth)
    assert docker.recreate_stop_argv(spec, services=[auth]) == [
        "compose",
        "stop",
        "-t",
        str(docker.STOP_GRACE_SECONDS),
        auth,
    ]
    assert spec.service_for(spec.world) not in docker.recreate_stop_argv(spec, services=[auth])


def test_opening_the_realm_keeps_other_flag_bits(monkeypatch, tmp_path) -> None:
    """A row with another flag bit set reads open: only bit 2 is the offline signal."""
    from yulon import realm_flag

    monkeypatch.setattr(
        docker, "status", lambda **kw: ["coa-database", "coa-worldserver", "coa-authserver"]
    )
    monkeypatch.setattr(
        docker,
        "sql_query",
        lambda container, client, password, schema, statement, **kw: "8",
    )
    (tmp_path / ".db_password").write_text("pw", encoding="utf-8")
    assert realm_flag.open_realm(COA, _spec(COA), tmp_path) is None


def test_opening_the_realm_reads_its_row_in_the_auth_schema(monkeypatch, tmp_path) -> None:
    """The live run (T812 C8): the row read ran with no schema: ERROR 1046 No database selected.

    The fake answers as MySQL does: a table named without a schema, on a connection
    that selected none, is refused. Mutation: pass None as the read's schema again.
    """
    from yulon import realm_flag

    monkeypatch.setattr(
        docker, "status", lambda **kw: ["coa-database", "coa-worldserver", "coa-authserver"]
    )
    asked: list[tuple[str | None, str]] = []

    def sql_query(container, client, password, schema, statement, **kw):  # type: ignore[no-untyped-def]
        asked.append((schema, statement))
        table = COA.realmlist.table
        qualified = f"{COA.databases.auth}.{table}" in statement
        if schema is None and not qualified:
            raise docker.DockerCommandError(
                "mysql in coa-database exited 1: ERROR 1046 (3D000) at line 1: No database selected"
            )
        return "0" if statement.lstrip().upper().startswith("SELECT") else ""

    monkeypatch.setattr(docker, "sql_query", sql_query)
    (tmp_path / ".db_password").write_text("pw", encoding="utf-8")
    assert realm_flag.open_realm(COA, _spec(COA), tmp_path) is None
    assert any(statement.lstrip().upper().startswith("SELECT") for _s, statement in asked)
