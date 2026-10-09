"""T607 item 5: Party, the command-channel setup and the bot-pool rebuild hold the server too.

T568 reserved the server (across processes) for the Applier's SQL and for the engine's presses.
Three feature paths write to a server's databases or files and are not Applier paths: Party's
account link, the command channel's account create/reset and its `enable`, and the Tortoise bot
pool rebuild (a conf write and a restart). Each takes an optional `hold_server` seam now, the
controller wires `docker.server_hold` into every construction, and a held server is a
sentence with nothing written.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from tests.test_install_channel import INSTALL, WOTLK, _Answering, _installed
from tests.test_party import WOTLK as WOTLK_ENTRY
from tests.test_party import _ready_install, _WriteSql
from tests.test_rebuild_random_bots import World, _conf
from yulon import channel_setup as setup
from yulon import docker, party, platform, resources
from yulon.controller_wow_tortoise import poolreset

HELD = "Another Yu'lon is working on WoW right now: “Rebuild the server…”. Nothing was changed."


class _Hold:
    """A `hold_server` seam that records its order against whatever else happens."""

    def __init__(self, events: list[str], *, refuse: bool = False) -> None:
        self.events = events
        self.refuse = refuse

    @contextmanager
    def __call__(self, press: str) -> Iterator[None]:
        if self.refuse:
            raise docker.ServerReserved(HELD, docker.ServerHolder("yulon-busy-x", "id"))
        self.events.append(f"hold:{press}")
        try:
            yield
        finally:
            self.events.append("release")


# ------------------------------------------------------------------ the helper


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text("yulon.local/wotlk-server:native\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


def test_server_hold_reserves_the_server_for_the_block_under_the_press_name(
    fake_docker: Path, tmp_path: Path
) -> None:
    from tests.support_fake_docker import containers

    server = tmp_path / "server"
    server.mkdir()
    spec = docker.ContainerSpec(db="ac-database", auth="ac-auth", world="ac-world", ports=(1,))
    with docker.server_hold(server, "Link accounts", spec=spec, label="WoW"):
        names = [n for n in containers(fake_docker) if n.startswith(docker.SERVER_CLAIM_PREFIX)]
        assert len(names) == 1
    assert [n for n in containers(fake_docker) if n.startswith(docker.SERVER_CLAIM_PREFIX)] == []


def test_server_hold_without_a_spec_holds_nothing(fake_docker: Path, tmp_path: Path) -> None:
    with docker.server_hold(tmp_path, "Link accounts", spec=None):
        pass


# ------------------------------------------------------------------ Party


def _install(server: Path, sql: Any, *, hold_server: Any) -> party.InstallParty:
    return party.InstallParty(
        WOTLK_ENTRY,
        server,
        sql=sql,
        channel_for_saved=lambda: None,
        container="ac-worldserver",
        world_running=lambda: False,
        engine=lambda: party.BinaryRead(True, "in"),
        link_writer=sql,
        hold_server=hold_server,
    )


def test_the_account_link_is_written_inside_the_hold_and_not_without_it(tmp_path: Path) -> None:
    events: list[str] = []
    sql = _WriteSql("1\tOWNER", "2\tFRIEND", "")
    real = sql.query

    def spied(db: str, statement: str) -> str:
        if "INSERT IGNORE" in statement:
            events.append("write")
        return real(db, statement)

    sql.query = spied  # type: ignore[method-assign]
    seam = _install(_ready_install(tmp_path), sql, hold_server=_Hold(events))
    assert seam.link_account("Pakka", "FRIEND").linked is True
    assert events == ["hold:Link accounts", "write", "release"]


def test_a_link_while_another_yulon_holds_the_server_writes_nothing(tmp_path: Path) -> None:
    sql = _WriteSql("1\tOWNER", "2\tFRIEND", "")
    seam = _install(_ready_install(tmp_path), sql, hold_server=_Hold([], refuse=True))
    result = seam.link_account("Pakka", "FRIEND")
    assert result.linked is False
    assert sql.written == []
    assert "Another Yu'lon is working on WoW" in result.sentence


# ------------------------------------------------------------------ the command channel


def _channel(tmp_path: Path, hold: Any, created: list[str]) -> setup.InstallChannel:
    return setup.InstallChannel(
        WOTLK,
        _installed(tmp_path),
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda name, pw, level: created.append(name),
        reset=lambda name, pw: created.append("reset:" + name),
        channel_for=lambda _endpoint: _Answering(["yes"]),
        config_dir=tmp_path / "config",
        hold_server=hold,
    )


def test_the_channels_account_is_created_inside_the_hold(tmp_path: Path) -> None:
    events: list[str] = []
    channel = _channel(tmp_path, _Hold(events), events)
    channel.prove()
    assert events[0].startswith("hold:") and "Set up the command channel" in events[0]
    assert setup.account_name(INSTALL) in events
    assert events.index(setup.account_name(INSTALL)) < events.index("release")


def test_the_channels_account_is_not_created_while_another_yulon_holds_the_server(
    tmp_path: Path,
) -> None:
    created: list[str] = []
    channel = _channel(tmp_path, _Hold([], refuse=True), created)
    channel.prove()
    assert created == []


def test_enable_writes_nothing_while_another_yulon_holds_the_server(tmp_path: Path) -> None:
    created: list[str] = []
    channel = _channel(tmp_path, _Hold([], refuse=True), created)
    server = channel.server_dir
    before = sorted(p.name for p in server.iterdir())
    with pytest.raises(setup.EnableRefused, match="Another Yu'lon is working on WoW"):
        channel.enable(world_running=False)
    assert sorted(p.name for p in server.iterdir()) == before


def test_enable_runs_inside_the_hold(tmp_path: Path) -> None:
    events: list[str] = []
    channel = _channel(tmp_path, _Hold(events), [])
    channel.enable(world_running=False)
    assert events == ["hold:Turn on the command channel", "release"]


# ------------------------------------------------------------------ the bot pool rebuild


def test_a_pool_rebuild_holds_the_server_from_before_the_backup_to_after_the_watch(
    tmp_path: Path,
) -> None:
    _conf(tmp_path)
    world = World(tmp_path)
    world.events.append("start")
    hold = _Hold(world.events)
    lines = list(
        world.rebuild(hold_server=hold).rebuild(backup=world.backup)  # type: ignore[arg-type]
    )
    assert lines
    assert world.events[1] == "hold:Rebuild random bots"
    assert world.events[-1] == "release"
    assert world.events.index("backup") < world.events.index("release")
    assert any(e.startswith("restart:") for e in world.events[:-1])


def test_a_pool_rebuild_while_another_yulon_holds_the_server_changes_nothing(
    tmp_path: Path,
) -> None:
    _conf(tmp_path)
    world = World(tmp_path)
    conf_before = (world.tmp / poolreset.bot_population.CONF_FILE).read_text()
    with pytest.raises(poolreset.PoolResetError, match="Another Yu'lon is working on WoW"):
        list(
            world.rebuild(hold_server=_Hold([], refuse=True)).rebuild(  # type: ignore[arg-type]
                backup=world.backup
            )
        )
    assert world.events == []
    assert (world.tmp / poolreset.bot_population.CONF_FILE).read_text() == conf_before


# ------------------------------------------------------------------ every construction is wired


@pytest.mark.parametrize("callee", ["InstallChannel", "InstallParty", "for_entry"], ids=lambda c: c)
def test_every_construction_in_the_controller_wires_the_hold(callee: str) -> None:
    """A new construction that forgets the seam would hold nothing, silently."""
    source = Path(resources.__file__).parent.joinpath("ui", "controller_view.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == callee)
            or (isinstance(node.func, ast.Name) and node.func.id == callee)
        )
        and (callee != "for_entry" or "poolreset" in ast.unparse(node.func))
    ]
    assert calls, f"no {callee}( call found: the guard has gone blind"
    unwired = [c.lineno for c in calls if "hold_server" not in {k.arg for k in c.keywords}]
    assert unwired == [], f"{callee}( at controller_view.py:{unwired} takes no hold_server="


def test_server_hold_refuses_with_the_holders_press_and_the_servers_name(
    fake_docker: Path, tmp_path: Path
) -> None:
    from tests.test_server_reservation import HOLDER_PRESS, _another_yulon_holds

    server = tmp_path / "server"
    server.mkdir()
    ident = docker.folder_id(server)
    spec = docker.ContainerSpec(db="ac-database", auth="ac-auth", world="ac-world", ports=(1,))
    theirs = _another_yulon_holds(fake_docker, docker.SERVER_CLAIM_PREFIX + str(ident))
    try:
        with (
            pytest.raises(docker.ServerReserved) as refused,
            docker.server_hold(server, "Link accounts", spec=spec, label="WoW Unbound"),
        ):
            pytest.fail("the block ran under another Yu'lon's reservation")
        assert HOLDER_PRESS in str(refused.value) and "WoW Unbound" in str(refused.value)
        assert "Link accounts" in str(refused.value), "the refused press is named"
    finally:
        theirs.kill()


def test_a_repair_while_another_yulon_holds_the_server_says_so_and_changes_nothing(
    tmp_path: Path,
) -> None:
    """Review of b66833f0: the hold's refusal was read as "the database could not be reached"
    ("Start the server") and the holder's sentence was lost."""
    from tests.test_install_channel import _save, _Scripted

    _save(tmp_path, password="stale")
    resets: list[str] = []
    channel = setup.InstallChannel(
        WOTLK,
        _installed(tmp_path),
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *a: resets.append("create"),
        reset=lambda name, pw: resets.append("reset"),
        channel_for=lambda _e: _Scripted(["no", "yes"]),
        config_dir=tmp_path / "config",
        hold_server=_Hold([], refuse=True),
    )
    channel.check()
    state = channel.repair()
    assert isinstance(state, setup.Refused)
    assert "Another Yu'lon is working on WoW" in state.reason, state.reason
    assert "database" not in state.reason.lower()
    assert resets == [], "the account was touched under another Yu'lon's job"
    assert state.password == "stale"
