"""T610 item 1: the Accounts, Characters, Tuning and channel writes take the server's hold too.

T568 reserved a server across processes for the engine's presses and the Applier's SQL, and T607
added Party, the command channel's account and `enable`, and the bot pool rebuild. These paths
were still unheld: the Accounts tab (create, password, GM level, delete), the Characters tab
(teleport, level, rename and the rest), the Tuning tab's conf saves and reverts, the channel's
roll-back. Each now writes inside `docker.server_hold`, and while another Yu'lon holds the server
it says that Yu'lon's sentence and writes nothing. A guard lists every write method and every
`ControllerView` writer, so a new one cannot forget.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from tests.test_controller_view import _Ps, _tuning_view
from tests.test_install_channel import INSTALL, WOTLK, _Answering, _channel
from yulon import channel_setup as setup
from yulon import docker, party, platform, play, resources, useraccounts
from yulon.catalog.catalog import load_catalog
from yulon.ui import controller_view as controller_view_module

HELD = "Another Yu'lon is working on WoW right now: “Rebuild the server…”. Nothing was changed."
CATALOG_WOTLK = load_catalog().get("wow-wotlk")


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    from yulon import runner
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


class _Hold:
    """A `hold_server` seam that records its order, or refuses like a held server."""

    def __init__(self, events: list[str] | None = None, *, refuse: bool = False) -> None:
        self.events = events if events is not None else []
        self.refuse = refuse

    @contextmanager
    def __call__(self, press: str, budget: float | None = None) -> Iterator[None]:
        if self.refuse:
            raise docker.ServerReserved(HELD, docker.ServerHolder("yulon-busy-x", "id"))
        self.events.append(f"hold:{press}")
        try:
            yield
        finally:
            self.events.append("release")


class _Nothing:
    """A SQL reader or channel that fails the test on any use: a held press sends nothing."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"something was asked of the server: {name}")


class _Reader:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def query(self, db: str, statement: str) -> str:
        self.events.append("read")
        return "Aevret\t1\n"

    def run_statement(self, db: str, statement: str) -> None:  # pragma: no cover
        raise AssertionError("no write expected")


class _Channel:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def send(self, command: str) -> object:
        self.events.append(f"send:{command}")
        return type("R", (), {"outcome": "yes", "text": "done"})()

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


# ------------------------------------------------------------------ the Accounts tab


def _accounts(tmp_path: Path, hold: Any, *, sql: Any = None, channel: Any = None) -> Any:
    return useraccounts.InstallAccounts(
        CATALOG_WOTLK,
        tmp_path,
        sql=sql if sql is not None else _Nothing(),
        channel_for_saved=lambda: channel if channel is not None else _Nothing(),
        app_account="YULON_AB12CD34",
        hold_server=hold,
    )


# What each write method is called with. A new public method of InstallAccounts that is neither
# a read nor listed here fails `test_every_account_method_is_a_read_or_holds`.
ACCOUNT_WRITES: dict[str, Callable[[], tuple[tuple[Any, ...], dict[str, Any]]]] = {
    "set_password": lambda: (("Pakka", "a-new-password"), {}),
    "set_gm_level": lambda: (("Pakka", 2), {}),
    "delete_account": lambda: ((useraccounts.DeletePlan("Pakka"),), {}),
}
ACCOUNT_READS = {"sql", "listing", "delete_plan"}
ACCOUNT_VIA_A_HELD_ONE = {"after_create": "its one send is set_gm_level, which holds"}


@pytest.mark.parametrize("method", sorted(ACCOUNT_WRITES))
def test_an_account_write_while_another_yulon_holds_the_server_sends_nothing(
    tmp_path: Path, method: str
) -> None:
    installed = _accounts(tmp_path, _Hold(refuse=True))
    args, kwargs = ACCOUNT_WRITES[method]()
    outcome = getattr(installed, method)(*args, **kwargs)
    assert outcome.done is False
    assert "Another Yu'lon is working on WoW" in outcome.problem
    assert outcome.indeterminate is False


def test_a_password_is_set_inside_the_hold(tmp_path: Path) -> None:
    events: list[str] = []
    installed = _accounts(tmp_path, _Hold(events), sql=_Reader(events), channel=_Channel(events))
    installed.set_password("Pakka", "a-new-password")
    assert events[0] == "hold:Change an account's password"
    assert events[-1] == "release"
    assert any(e.startswith("send:") for e in events[1:-1])


def test_every_account_method_is_a_read_or_holds() -> None:
    """Mutation: add a public write method without `@holding`, or drop it from a held one."""
    public = {
        name
        for name, member in inspect.getmembers(useraccounts.InstallAccounts)
        if not name.startswith("_") and (callable(member) or isinstance(member, property))
    }
    unaccounted = public - ACCOUNT_READS - set(ACCOUNT_WRITES) - set(ACCOUNT_VIA_A_HELD_ONE)
    assert unaccounted == set(), f"{sorted(unaccounted)}: a read, or a write that holds the server?"
    for name in ACCOUNT_WRITES:
        method = getattr(useraccounts.InstallAccounts, name)
        assert getattr(method, "server_hold_press", None), f"{name} does not take the hold"


# ------------------------------------------------------------------ the Characters tab


def _characters(tmp_path: Path, hold: Any, *, sql: Any = None, channel: Any = None) -> Any:
    return play.InstallPlay(
        CATALOG_WOTLK,
        tmp_path,
        sql=sql if sql is not None else _Nothing(),
        channel_for_saved=lambda: channel if channel is not None else _Nothing(),
        hold_server=hold,
    )


CHARACTER_WRITES: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "teleport": (("Aevret", "Stormwind"), {}),
    "set_level": (("Aevret", 60), {}),
    "set_level_and_save": (("Aevret", 60), {}),
    "rename": (("Aevret",), {}),
    "revive": (("Aevret",), {}),
    "mail_gold": (("Aevret",), {"gold": 1, "subject": "s", "body": "b"}),
    "mail_items": (("Aevret",), {"items": ((1, 1),), "subject": "s", "body": "b"}),
    "send_gear_set": (("Aevret",), {"to": "Pakka", "subject": "s", "body": "b"}),
}
CHARACTER_READS = {
    "mail_item_cap",
    "for_entry_is_possible",
    "listing",
    "find_items",
    "gear_set_size",
}


@pytest.mark.parametrize("method", sorted(CHARACTER_WRITES))
def test_a_character_write_while_another_yulon_holds_the_server_sends_nothing(
    tmp_path: Path, method: str
) -> None:
    installed = _characters(tmp_path, _Hold(refuse=True))
    args, kwargs = CHARACTER_WRITES[method]
    outcome = getattr(installed, method)(*args, **kwargs)
    assert outcome.done is False
    assert "Another Yu'lon is working on WoW" in outcome.problem


def test_a_teleport_is_sent_inside_the_hold(tmp_path: Path) -> None:
    events: list[str] = []
    installed = _characters(tmp_path, _Hold(events), sql=_Reader(events), channel=_Channel(events))
    installed.teleport("Aevret", "Stormwind")
    assert events[0] == "hold:Teleport a character"
    assert events[-1] == "release"
    assert any(e.startswith("send:") for e in events[1:-1])


def test_every_character_method_is_a_read_or_holds() -> None:
    """Mutation: add a public write method without `@holding`, or drop it from a held one."""
    public = {
        name
        for name, member in inspect.getmembers(play.InstallPlay)
        if not name.startswith("_") and (callable(member) or isinstance(member, property))
    }
    unaccounted = public - CHARACTER_READS - set(CHARACTER_WRITES)
    assert unaccounted == set(), f"{sorted(unaccounted)}: a read, or a write that holds the server?"
    for name in CHARACTER_WRITES:
        method = getattr(play.InstallPlay, name)
        assert getattr(method, "server_hold_press", None), f"{name} does not take the hold"


# ------------------------------------------------------------------ every construction is wired


def _constructions(callee: str) -> list[tuple[str, int, bool]]:
    """Every call of `callee` in the `yulon` package: (file, line, passes `hold_server=`)."""
    found: list[tuple[str, int, bool]] = []
    for path in sorted(Path(resources.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if name == callee:
                held = "hold_server" in {k.arg for k in node.keywords}
                found.append((path.name, node.lineno, held))
    return found


@pytest.mark.parametrize("callee", ["InstallAccounts", "InstallPlay"])
def test_every_construction_anywhere_in_yulon_wires_the_hold(callee: str) -> None:
    """Every module, not just the controller's: My Party builds an InstallPlay of its own."""
    calls = _constructions(callee)
    assert {name for name, _line, _held in calls} >= {"controller_view.py"}, "the guard is blind"
    unwired = [f"{name}:{line}" for name, line, held in calls if not held]
    assert unwired == [], f"{callee}( takes no hold_server= at {unwired}"


def test_my_partys_bot_level_setter_writes_inside_the_hold_and_not_without_it(
    tmp_path: Path,
) -> None:
    """Mutation: build the party's own `InstallPlay` without `hold_server=`."""
    from tests.test_party import WOTLK as PARTY_WOTLK
    from tests.test_party import _ready_install, _WriteSql

    sql = _WriteSql("1\tOWNER", "2\tFRIEND", "")
    seam = party.InstallParty(
        PARTY_WOTLK,
        _ready_install(tmp_path),
        sql=sql,
        channel_for_saved=lambda: _Nothing(),
        container="ac-worldserver",
        world_running=lambda: False,
        engine=lambda: party.BinaryRead(True, "in"),
        link_writer=sql,
        hold_server=_Hold(refuse=True),
    )
    asked = len(sql.asked)
    outcome = seam.level_setter("Aevret", 60)
    assert outcome.done is False and "Another Yu'lon is working on WoW" in outcome.problem
    assert sql.written == []
    assert len(sql.asked) == asked, "the server was asked"


# ------------------------------------------------------------------ the account create seam


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text("yulon.local/wotlk-server:native\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


def test_the_account_create_seam_is_made_under_the_hold_by_the_shared_assembly(
    fake_docker: Path, tmp_path: Path
) -> None:
    """Mutation: pass `create_account` through unwrapped in `_assemble` and this writes."""
    from tests.test_server_reservation import HOLDER_PRESS, _another_yulon_holds

    server = tmp_path / "server"
    server.mkdir()
    ident = docker.folder_id(server)
    made: list[str] = []
    services = controller_view_module._assemble(
        CATALOG_WOTLK,
        server,
        client_dir=None,
        wsl_distro=None,
        controller=_Nothing(),  # type: ignore[arg-type]
        sql=_Nothing(),  # type: ignore[arg-type]
        send_console=lambda _c: None,  # type: ignore[arg-type,return-value]
        create_account=lambda name, pw, level: made.append(name),  # type: ignore[arg-type,return-value]
        store=None,
        applier=None,
        backup=lambda: None,  # type: ignore[arg-type,return-value]
        plan_restore=lambda *_a: None,  # type: ignore[arg-type,return-value]
        restore=lambda _p: None,  # type: ignore[arg-type,return-value]
    )
    theirs = _another_yulon_holds(fake_docker, docker.SERVER_CLAIM_PREFIX + str(ident))
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            services.create_account("Pakka", "pw", 0)
        assert HOLDER_PRESS in str(refused.value)
        assert "Create an account" in str(refused.value)
    finally:
        theirs.kill()
    assert made == []


def test_the_account_create_seam_runs_when_nobody_holds_the_server(
    fake_docker: Path, tmp_path: Path
) -> None:
    server = tmp_path / "server"
    server.mkdir()
    made: list[str] = []
    services = controller_view_module._assemble(
        CATALOG_WOTLK,
        server,
        client_dir=None,
        wsl_distro=None,
        controller=_Nothing(),  # type: ignore[arg-type]
        sql=_Nothing(),  # type: ignore[arg-type]
        send_console=lambda _c: None,  # type: ignore[arg-type,return-value]
        create_account=lambda name, pw, level: made.append(name),  # type: ignore[arg-type,return-value]
        store=None,
        applier=None,
        backup=lambda: None,  # type: ignore[arg-type,return-value]
        plan_restore=lambda *_a: None,  # type: ignore[arg-type,return-value]
        restore=lambda _p: None,  # type: ignore[arg-type,return-value]
    )
    services.create_account("Pakka", "pw", 0)
    assert made == ["Pakka"]


# ------------------------------------------------------------------ the views


def test_the_accounts_tab_says_the_holders_sentence_alone_when_a_create_is_refused(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    from tests.test_controller_view import _services

    services = _services(ps, tmp_path, [])

    def refused(_name: str, _pw: str, _level: int) -> object:
        raise docker.ServerReserved(HELD, docker.ServerHolder("yulon-busy-x", "id"))

    object.__setattr__(services, "create_account", refused)
    view = controller_view_module.ControllerView(CATALOG_WOTLK, services, status_poll_ms=0)
    view.account_name.setText("Pakka")
    view.account_password.setText("pw")
    view.create_account()
    assert view.account_report.text() == HELD
    assert view.create_account_button.isEnabled()


TUNING_FILE = "env/dist/etc/modules/mod_npc_beastmaster.conf"


def _tuning_with_hold(
    ps: _Ps, tmp_path: Path, hold: _Hold
) -> controller_view_module.ControllerView:
    view = _tuning_view(ps, tmp_path)
    object.__setattr__(view.services, "hold_server", hold)
    return view


def _conf(tmp_path: Path) -> str:
    return (tmp_path / TUNING_FILE).read_text(encoding="utf-8")


def test_a_raw_conf_save_is_written_inside_the_hold(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    events: list[str] = []
    view = _tuning_with_hold(ps, tmp_path, _Hold(events))
    view.open_tuning_file(TUNING_FILE)
    view.save_tuning_file("BeastMaster.Enable = 0\n")
    assert events == ["hold:Save a setting file", "release"]
    assert _conf(tmp_path) == "BeastMaster.Enable = 0\n"


def test_a_raw_conf_save_while_another_yulon_holds_the_server_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuning_with_hold(ps, tmp_path, _Hold(refuse=True))
    view.open_tuning_file(TUNING_FILE)
    failed: list[str] = []
    view.action_failed.connect(failed.append)
    view.save_tuning_file("BeastMaster.Enable = 0\n")
    assert _conf(tmp_path) == "BeastMaster.Enable = 1\n"
    assert view.tuning_report.toPlainText() == HELD
    assert failed == [HELD]
    assert not list(tmp_path.glob("**/*.bak")), "not even a backup copy"


def test_a_card_save_while_another_yulon_holds_the_server_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuning_with_hold(ps, tmp_path, _Hold(refuse=True))
    card = view.tuning_panel.card("mod-npc-beastmaster")
    card.editors["BeastMaster.Enable"].control.setChecked(False)
    assert card.save_button is not None
    card.save_button.click()
    assert _conf(tmp_path) == "BeastMaster.Enable = 1\n"
    assert view.tuning_report.toPlainText() == HELD
    assert not list(tmp_path.glob("**/*.bak"))


def test_a_card_save_is_written_inside_the_hold(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    events: list[str] = []
    view = _tuning_with_hold(ps, tmp_path, _Hold(events))
    card = view.tuning_panel.card("mod-npc-beastmaster")
    card.editors["BeastMaster.Enable"].control.setChecked(False)
    assert card.save_button is not None
    card.save_button.click()
    assert events == ["hold:Save settings", "release"]
    assert _conf(tmp_path) == "BeastMaster.Enable = 0\n"


def _saved_once_with_a_backup(view: controller_view_module.ControllerView, tmp_path: Path) -> None:
    view.open_tuning_file(TUNING_FILE)
    view.save_tuning_file("BeastMaster.Enable = 0\n")
    assert _conf(tmp_path) == "BeastMaster.Enable = 0\n"


def test_a_revert_while_another_yulon_holds_the_server_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _tuning_with_hold(ps, tmp_path, _Hold())
    _saved_once_with_a_backup(view, tmp_path)
    object.__setattr__(view.services, "hold_server", _Hold(refuse=True))
    view.revert_tuning_file()
    assert _conf(tmp_path) == "BeastMaster.Enable = 0\n"
    assert HELD in view.tuning_report.toPlainText()
    card_view = view.tuning_panel.card("mod-npc-beastmaster")
    assert card_view is not None
    card = card_view.card
    view.revert_tuning(card.family, card.module_id)
    assert _conf(tmp_path) == "BeastMaster.Enable = 0\n"
    assert view.tuning_report.toPlainText() == HELD


def test_a_revert_is_written_inside_the_hold(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    events: list[str] = []
    view = _tuning_with_hold(ps, tmp_path, _Hold(events))
    _saved_once_with_a_backup(view, tmp_path)
    events.clear()
    view.revert_tuning_file()
    assert _conf(tmp_path) == "BeastMaster.Enable = 1\n"
    assert events == ["hold:Put a setting file back", "release"]


def test_every_controller_view_method_that_writes_a_conf_takes_the_hold() -> None:
    """Mutation: call `tuning.write` / `tuning.backup` / `_put_back` from a method with no hold."""
    source = Path(resources.__file__).parent.joinpath("ui", "controller_view.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    view = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ControllerView"
    )
    writers = {
        "tuning.write",
        "tuning.backup",
        "tuning.restore",
        "unbound_settings.write",
        "self._put_back",
    }
    found: dict[str, bool] = {}
    for method in view.body:
        if not isinstance(method, ast.FunctionDef) or method.name == "_put_back":
            continue
        calls = {ast.unparse(n.func) for n in ast.walk(method) if isinstance(n, ast.Call)}
        if calls & writers:
            found[method.name] = "self._writing_to_the_server" in calls
    assert set(found) >= {"save_tuning", "save_tuning_file", "revert_tuning", "revert_tuning_file"}
    assert [name for name, held in found.items() if not held] == []


# ------------------------------------------------------------------ the channel


def test_a_roll_back_while_another_yulon_holds_the_server_changes_nothing(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path, answering=_Answering("yes"))
    channel.enable(world_running=False)
    override = channel.server_dir / "docker-compose.override.yml"
    written = override.read_text(encoding="utf-8")
    held = setup.InstallChannel(
        WOTLK,
        channel.server_dir,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *_a: None,
        reset=lambda *_a: None,
        channel_for=lambda _e: _Answering("yes"),
        config_dir=tmp_path / "config",
        hold_server=_Hold(refuse=True),
    )
    with pytest.raises(setup.ServerHeldElsewhere, match="Another Yu'lon is working on WoW"):
        held.roll_back()
    assert override.read_text(encoding="utf-8") == written


def test_a_roll_back_is_made_inside_the_hold(tmp_path: Path) -> None:
    events: list[str] = []
    channel = setup.InstallChannel(
        WOTLK,
        _channel(tmp_path, answering=_Answering("yes")).server_dir,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *_a: None,
        reset=lambda *_a: None,
        channel_for=lambda _e: _Answering("yes"),
        config_dir=tmp_path / "config",
        hold_server=_Hold(events),
    )
    channel.enable(world_running=False)
    events.clear()
    assert channel.roll_back() is True
    assert events == ["hold:Turn the command channel back off", "release"]
