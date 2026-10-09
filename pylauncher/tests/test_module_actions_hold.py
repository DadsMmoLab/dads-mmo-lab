"""T607 item 2: a Modules press holds the server for the whole action, not for its SQL only.

T568 held the server (across processes) around a module's direct SQL. The same press also
clones into `modules/`, copies files into the server folder, makes folders and edits conf
files; another Yu'lon's Rebuild or Update reads exactly those while it builds. The hold now
covers the whole of an Install, a Remove and a Configure -- Update runs Install -- and the SQL
inside it shares that one hold.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.test_apply import STACKABLES, _FakeGit, _FakeSql
from yulon import docker
from yulon.apply import Applier, ApplyRefusal
from yulon.manifest import parse_manifest

NO_SQL: dict[str, Any] = {
    "id": "lua-pack",
    "name": "Lua Pack",
    "type": "mod",
    "game": "wow-wotlk",
    "source": {"repo": "DadsMmoLab/dads-mmo-lab", "sparse_path": "mods/lua-pack"},
    "deploy": [{"src": "scripts", "dest": "env/dist/etc/lua_scripts"}],
    "folders": ["env/dist/etc/extra_scripts"],
}


class _Spy:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.lost = threading.Event()

    @contextmanager
    def hold(self, press: str) -> Iterator[Any]:
        self.events.append(f"hold:{press}")
        try:
            yield type("Held", (), {"lost": self.lost})()
        finally:
            self.events.append("release")


def _applier(tmp_path: Path, spy: _Spy, manifest: dict[str, Any], *, hold: Any = "spy") -> Applier:
    parsed = parse_manifest(manifest)
    assert parsed.source is not None
    files = {"scripts/a.lua": "print('a')\n", "up.sql": "UPDATE t SET a = 1;\n", "down.sql": "--\n"}
    git = _FakeGit(files, unmodified=True, no_local_commits=True)
    git_events = spy.events
    real_clone = git.clone

    def clone(spec: Any) -> None:
        git_events.append("clone")
        real_clone(spec)

    git.clone = clone  # type: ignore[method-assign]
    return Applier(
        tmp_path,
        git=git,
        sql=_FakeSql(),
        world_running=lambda: False,
        remote_url=lambda _dest: parsed.source.url,  # type: ignore[union-attr]
        hold_server=spy.hold if hold == "spy" else hold,
    )


@contextmanager
def _held_elsewhere(press: str) -> Iterator[None]:
    raise docker.ServerReserved(
        "Another Yu'lon is working on WoW right now: “Rebuild the server…”. Nothing was changed.",
        docker.ServerHolder("yulon-busy-x", "id"),
    )
    yield


def test_an_install_with_no_sql_holds_the_server_from_before_the_clone(tmp_path: Path) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL)
    applier.install(parse_manifest(NO_SQL))
    assert spy.events[0] == "hold:Install Lua Pack"
    assert spy.events[1] == "clone"
    assert spy.events[-1] == "release"
    assert [e for e in spy.events if e.startswith("hold")] == ["hold:Install Lua Pack"]
    assert (tmp_path / "env/dist/etc/lua_scripts/a.lua").is_file()
    assert (tmp_path / "env/dist/etc/extra_scripts").is_dir()


def test_an_install_refused_by_the_hold_clones_copies_and_makes_nothing(tmp_path: Path) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL, hold=_held_elsewhere)
    with pytest.raises(ApplyRefusal) as refused:
        applier.install(parse_manifest(NO_SQL))
    assert "Another Yu'lon is working on WoW" in str(refused.value)
    assert "clone" not in spy.events
    assert list(tmp_path.iterdir()) == [], "something was written before the hold"


def test_a_remove_holds_the_server_before_it_takes_anything_back(tmp_path: Path) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL)
    applier.install(parse_manifest(NO_SQL))
    spy.events.clear()
    applier.remove(parse_manifest(NO_SQL))
    assert spy.events == ["hold:Remove Lua Pack", "release"]
    assert not (tmp_path / "env/dist/etc/lua_scripts/a.lua").exists()


def test_a_remove_refused_by_the_hold_takes_nothing_back(tmp_path: Path) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL)
    applier.install(parse_manifest(NO_SQL))
    held = _applier(tmp_path, spy, NO_SQL, hold=_held_elsewhere)
    with pytest.raises(ApplyRefusal):
        held.remove(parse_manifest(NO_SQL))
    assert (tmp_path / "env/dist/etc/lua_scripts/a.lua").is_file()
    assert applier.clone_dir(parse_manifest(NO_SQL)).is_dir()


def test_a_configure_holds_the_server_too(tmp_path: Path) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL)
    applier.configure(parse_manifest(NO_SQL))
    assert spy.events == ["hold:Configure Lua Pack", "release"]


def test_an_update_holds_once_and_its_sql_shares_that_hold(tmp_path: Path) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, STACKABLES)
    applier.install(parse_manifest(STACKABLES))
    spy.events.clear()
    applier.update(parse_manifest(STACKABLES))
    holds = [e for e in spy.events if e.startswith("hold")]
    assert holds == ["hold:Update All Stackables"], spy.events
    assert spy.events[0] == holds[0] and spy.events.count("release") == 1


def test_the_sql_inside_the_action_still_stops_when_the_hold_is_lost(tmp_path: Path) -> None:
    """The loss event of the whole-action hold is the one the SQL reads between statements."""
    spy = _Spy()
    spy.lost.set()
    applier = _applier(tmp_path, spy, STACKABLES)
    with pytest.raises(Exception) as stopped:
        applier.install(parse_manifest(STACKABLES))
    assert "stopped this server" in str(stopped.value) or "reservation" in str(stopped.value)
    assert applier.sql.files == [] and applier.sql.statements == []  # type: ignore[attr-defined]


def test_without_a_hold_seam_nothing_is_held(tmp_path: Path) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL, hold=None)
    applier.install(parse_manifest(NO_SQL))
    assert [e for e in spy.events if e.startswith("hold")] == []


def test_a_put_back_holds_the_server_and_a_refused_one_moves_nothing(tmp_path: Path) -> None:
    from tests.test_apply_put_back import _installed_at_a, _publish, _rig

    rig = _rig(tmp_path)
    a = _installed_at_a(rig)
    _publish(rig.origin, "B")
    rig.applier.update(rig.manifest)
    last = rig.applier.last_update(rig.manifest)
    assert last is not None
    spy = _Spy()
    rig.applier._hold_server = _held_elsewhere
    head = rig.head()
    with pytest.raises(ApplyRefusal):
        rig.applier.put_back(rig.manifest, last=last)
    assert rig.head() == head, "a refused put-back moved the checkout"
    rig.applier._hold_server = spy.hold
    rig.applier.put_back(rig.manifest, last=last)
    assert rig.head() == a
    assert spy.events == [f"hold:Put back {rig.manifest.name}", "release"]


# ---------------------------------------- review of b66833f0: "Stop anyway" ends the whole action


def test_an_install_stops_between_its_steps_once_the_hold_is_lost(tmp_path: Path) -> None:
    """The loss was read only by the SQL: the clone's deploy, folders and conf went on."""
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL)
    real_clone = applier.git.clone

    def clone_then_lose(spec: Any) -> None:
        real_clone(spec)
        spy.lost.set()  # another Yu'lon's "Stop anyway", right after the clone

    applier.git.clone = clone_then_lose  # type: ignore[method-assign]
    with pytest.raises(ApplyRefusal) as stopped:
        applier.install(parse_manifest(NO_SQL))
    assert "stopped this server" in str(stopped.value)
    assert not (tmp_path / "env/dist/etc/lua_scripts/a.lua").exists(), "the deploy still ran"
    assert not (tmp_path / "env/dist/etc/extra_scripts").exists(), "the folders were still made"


def test_an_action_whose_hold_was_already_lost_does_nothing_at_all(tmp_path: Path) -> None:
    spy = _Spy()
    spy.lost.set()
    applier = _applier(tmp_path, spy, NO_SQL)
    for action in (applier.install, applier.configure, applier.remove):
        with pytest.raises(ApplyRefusal):
            action(parse_manifest(NO_SQL))
    assert "clone" not in spy.events
    assert list(tmp_path.iterdir()) == []


def test_a_remove_stops_between_its_steps_once_the_hold_is_lost(tmp_path: Path) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL)
    applier.install(parse_manifest(NO_SQL))
    real_undeploy = applier._undeploy

    def lose_then_undeploy(*args: Any) -> None:
        spy.lost.set()
        real_undeploy(*args)

    applier._undeploy = lose_then_undeploy  # type: ignore[method-assign]
    with pytest.raises(ApplyRefusal):
        applier.remove(parse_manifest(NO_SQL))
    assert applier.clone_dir(
        parse_manifest(NO_SQL)
    ).is_dir(), "the clone was deleted after the loss"


def _lose_after(applier: Applier, spy: _Spy, name: str, nth: int = 1) -> list[str]:
    """Make the hold lost right after the `nth` call of the applier's step `name`.

    Returns the list that records every step called, by name, so a test can say which step must
    NOT have run once the loss was known.
    """
    calls: list[str] = []
    for each in (
        "_deploy _folders _patches _sql _conf _client _dbc _finish_claim _undeploy _unfolders "
        "_unclient _refuse_checkout_links"
    ).split():
        real = getattr(applier, each)
        seen = {"n": 0}

        def wrapped(
            *args: Any, _real: Any = real, _name: str = each, _seen: Any = seen, **kwargs: Any
        ) -> Any:
            calls.append(_name)
            _seen["n"] += 1
            out = _real(*args, **kwargs)
            if _name == name and _seen["n"] == nth:
                spy.lost.set()
            return out

        setattr(applier, each, wrapped)
    return calls


# (action, step after which the hold is lost, its nth call, the step that must not run after it)
BETWEEN_STEPS = [
    ("install", "_deploy", 1, "_folders"),
    ("install", "_folders", 1, "_patches"),
    ("install", "_sql", 1, "_conf"),
    ("install", "_conf", 1, "_patches"),
    ("install", "_sql", 2, "_client"),
    ("install", "_dbc", 1, "_finish_claim"),
    ("configure", "_refuse_checkout_links", 1, "_patches"),
    ("configure", "_sql", 1, "_conf"),
    ("remove", "_refuse_checkout_links", 1, "_patches"),
    ("remove", "_sql", 1, "_undeploy"),
    ("remove", "_undeploy", 1, "_unfolders"),
    ("remove", "_unfolders", 1, "_unclient"),
    ("remove", "_unclient", 1, "rmtree"),
]


@pytest.mark.parametrize(("action", "after", "nth", "target"), BETWEEN_STEPS)
def test_each_step_of_an_action_checks_the_hold_before_it_runs(
    tmp_path: Path, action: str, after: str, nth: int, target: str
) -> None:
    spy = _Spy()
    applier = _applier(tmp_path, spy, NO_SQL)
    manifest = parse_manifest(NO_SQL)
    if action == "remove":
        applier.install(manifest)
    calls = _lose_after(applier, spy, after, nth)
    with pytest.raises(ApplyRefusal) as stopped:
        getattr(applier, action)(manifest)
    assert "stopped this server" in str(stopped.value)
    positions = [i for i, called in enumerate(calls) if called == after]
    assert len(positions) >= nth, f"{after} ran {len(positions)} time(s): the case tests nothing"
    if target == "rmtree":
        assert applier.clone_dir(manifest).is_dir(), "the clone was deleted after the loss"
    else:
        assert target not in calls[positions[nth - 1] + 1 :], calls
