"""T552: a second AzerothCore server is a server of its own, beside WotLK.

Docker container names are global to a daemon, so two installs that both
name `ac-database` cannot exist at once, even with one of them stopped. The
three WotLK templates spelled every `ac-*` name as a literal; they now take the
entry's prefix, and WotLK's own render stays the committed bytes
(`test_composegen.py::test_the_wotlk_render_reproduces_the_committed_snapshot_byte_for_byte`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tests.support_second_ac import (
    SECOND_CONTAINERS,
    SECOND_PORTS,
    SECOND_SOAP,
    second_ac_entry,
    second_ac_json,
    wotlk_json,
)
from yulon import resources
from yulon.catalog import composegen
from yulon.catalog.catalog import Catalog, load_catalog, parse_catalog

TEMPLATES = resources.installers_dir()
WOTLK_NAMES = ("ac-database", "ac-authserver", "ac-worldserver", "ac-db-import", "ac-client-data")


def render(tmp_path: Path) -> composegen.ComposePlan:
    return composegen.render(
        second_ac_entry(),
        tmp_path / "yulon-second-ac",
        templates_root=TEMPLATES,
        platform_id=lambda: "linux",
    )


def code_lines(text: str) -> str:
    """The file without its comment lines: prose about WotLK's history may name `ac-*`."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_a_second_entry_renders_its_own_services_and_container_names(tmp_path: Path) -> None:
    plan = render(tmp_path)
    base = yaml.safe_load(plan.base)
    expected = {
        SECOND_CONTAINERS["db"],
        SECOND_CONTAINERS["auth"],
        SECOND_CONTAINERS["world"],
        SECOND_CONTAINERS["db_import"],
        SECOND_CONTAINERS["client_data"],
    }
    assert set(base["services"]) == expected
    for key, service in base["services"].items():
        assert service["container_name"] == key
        for needed in service.get("depends_on", {}):
            assert needed in expected, (key, needed)
    assert set(yaml.safe_load(plan.override)["services"]) == {SECOND_CONTAINERS["world"]}
    assert set(yaml.safe_load(plan.build)["services"]) == expected - {SECOND_CONTAINERS["db"]}


def test_no_wotlk_name_is_left_outside_a_comment(tmp_path: Path) -> None:
    plan = render(tmp_path)
    for name, text in (("base", plan.base), ("override", plan.override), ("build", plan.build)):
        for wotlk in WOTLK_NAMES:
            assert wotlk not in code_lines(text), (name, wotlk)


def test_every_server_reaches_its_own_database(tmp_path: Path) -> None:
    base = yaml.safe_load(render(tmp_path).base)
    db = SECOND_CONTAINERS["db"]
    infos = [
        value
        for service in base["services"].values()
        for key, value in (service.get("environment") or {}).items()
        if key.endswith("_DATABASE_INFO")
    ]
    assert len(infos) == 9
    assert all(info.startswith(f"{db};3306;") for info in infos), infos
    assert f"-h {db} " in base["services"][db]["healthcheck"]["test"]


def test_a_second_entry_publishes_its_own_host_ports(tmp_path: Path) -> None:
    base = yaml.safe_load(render(tmp_path).base)
    services = base["services"]
    assert services[SECOND_CONTAINERS["auth"]]["ports"] == [
        f"${{DOCKER_AUTH_EXTERNAL_PORT:-{SECOND_PORTS['auth']}}}:3724"
    ]
    assert services[SECOND_CONTAINERS["world"]]["ports"] == [
        f"${{DOCKER_WORLD_EXTERNAL_PORT:-{SECOND_PORTS['world']}}}:8085",
        f"${{DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:{SECOND_SOAP}}}:7878",
    ]
    assert services[SECOND_CONTAINERS["db"]]["ports"] == [
        f"127.0.0.1:${{DOCKER_DB_EXTERNAL_PORT:-{SECOND_PORTS['db']}}}:3306"
    ]


def test_the_two_projects_share_no_name_docker_holds_globally(tmp_path: Path) -> None:
    """Containers are global; networks and volumes are keyed by the project name."""
    wotlk = composegen.render(
        load_catalog().get("wow-wotlk"),
        tmp_path / "yulon-wotlk",
        templates_root=TEMPLATES,
        platform_id=lambda: "linux",
    )
    second = render(tmp_path)

    def globals_of(text: str) -> set[str]:
        doc = yaml.safe_load(text)
        return {s["container_name"] for s in doc["services"].values()} | {doc["name"]}

    assert not globals_of(wotlk.base) & globals_of(second.base)


# -- what the catalog may say -------------------------------------------------


@pytest.mark.parametrize("field", ["db_import", "client_data"])
def test_an_azerothcore_one_shot_off_the_prefix_is_refused(field: str) -> None:
    """The template derives all five names from one prefix; the entry may not say otherwise.

    The engine selects `db_import` and `client_data` by name, so a name the
    rendered file does not define is `no such service` after a multi-hour build.
    """
    containers = dict(SECOND_CONTAINERS)
    containers[field] = "ac-" + containers[field].removeprefix("ub-")
    with pytest.raises(ValidationError, match=field):
        second_ac_entry(containers=containers)


def test_an_azerothcore_server_container_off_the_template_names_is_refused() -> None:
    containers = dict(SECOND_CONTAINERS, world="ub-world")
    with pytest.raises(ValidationError, match="ub-worldserver"):
        second_ac_entry(containers=containers)


def test_two_entries_naming_one_container_are_refused() -> None:
    """A container name is global to the daemon, so the catalog may hand it out once."""
    clash = second_ac_json(
        containers={
            "db": "ac-database",
            "auth": "ac-authserver",
            "world": "ac-worldserver",
            "db_import": "ac-db-import",
            "client_data": "ac-client-data-init",
        }
    )
    with pytest.raises(ValidationError, match="ac-database"):
        parse_catalog({"games": [wotlk_json(), clash]})


def test_two_azerothcore_entries_with_their_own_names_load_together() -> None:
    both = parse_catalog({"games": [wotlk_json(), second_ac_json()]})
    assert isinstance(both, Catalog)
    assert [g.id for g in both.games] == ["wow-wotlk", "wow-second-ac"]


def test_two_entries_that_can_run_together_may_not_share_a_database_port() -> None:
    """Their auth and world ports differ, so both may run; a shared DB port would fail late."""
    clash = second_ac_json(ports={**SECOND_PORTS, "db": 3306})
    with pytest.raises(ValidationError, match="3306"):
        parse_catalog({"games": [wotlk_json(), clash]})


def test_two_entries_that_can_run_together_may_not_share_a_soap_port() -> None:
    clash = second_ac_json()
    clash["install"]["native"]["soap_port"] = 7878
    clash["operations"]["port"] = 7878
    with pytest.raises(ValidationError, match="7878"):
        parse_catalog({"games": [wotlk_json(), clash]})


def test_two_entries_that_share_an_auth_port_may_share_the_rest() -> None:
    """They conflict on purpose (Start offers to stop the other), so one DB/SOAP port is fine."""
    sibling = second_ac_json(ports={**SECOND_PORTS, "auth": 3724, "db": 3306})
    sibling["install"]["native"]["soap_port"] = 7878
    sibling["operations"]["port"] = 7878
    parse_catalog({"games": [wotlk_json(), sibling]})


def test_the_shipped_catalog_names_every_container_once() -> None:
    seen: dict[str, str] = {}
    for entry in load_catalog().games:
        c = entry.containers
        for name in (c.db, c.auth, c.world, c.db_import, c.client_data):
            if name is None:
                continue
            assert name not in seen, (name, seen.get(name), entry.id)
            seen[name] = entry.id


def test_wotlk_templates_spell_no_container_name_outside_a_comment() -> None:
    """The guard on the templates themselves: a literal re-added would pass WotLK's snapshot."""
    root = TEMPLATES / "wow-wotlk" / "native"
    for name in ("base.yml.tmpl", "override.yml.tmpl", "build.yml.tmpl"):
        text = code_lines((root / name).read_text(encoding="utf-8"))
        hits = [w for w in WOTLK_NAMES if re.search(re.escape(w), text)]
        assert not hits, (name, hits)


def test_a_soap_port_the_channel_does_not_dial_is_refused() -> None:
    """The base file publishes `install.native.soap_port`; the channel dials `operations.port`."""
    raw = second_ac_json()
    raw["operations"]["port"] = 7878
    with pytest.raises(ValidationError, match="soap_port"):
        parse_catalog({"games": [raw]})


# -- the realm row and the client say the second server's own ports ------------


def _install_second(tmp_path: Path, **recorder: object) -> tuple[list[str], object]:
    from tests.support_native import Recorder
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller
    from yulon.catalog.installer import InstallOptions

    recorder.setdefault("realm_port", "8086\n")
    rec = Recorder(**recorder)  # type: ignore[arg-type]
    engine = AzerothCoreInstaller(
        second_ac_entry(),
        installers_root=TEMPLATES,
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )
    return list(engine.run(InstallOptions(server_dir=tmp_path / "srv"))), rec


PORT_SQL = "UPDATE acore_auth.realmlist SET port=8086 WHERE id=1 AND port<>8086;"


def test_the_realm_row_is_given_the_world_port_before_the_first_start(tmp_path: Path) -> None:
    """The authserver hands clients the row's port, and prints it once at its first start.

    The import seeds 8085, WotLK's port, so without this a client logging in to
    the second server is sent to WotLK's world, and the ready wait for
    `<addr>:8086` never matches what the authserver printed.
    """
    lines, rec = _install_second(tmp_path)
    assert PORT_SQL in rec.sql_scripts  # type: ignore[attr-defined]
    calls = rec.calls  # type: ignore[attr-defined]
    assert calls.index("sql") < calls.index("start"), calls
    assert any("8086" in line and "realm" in line for line in lines), lines


def test_a_wotlk_install_sends_no_port_statement(tmp_path: Path) -> None:
    """8085 is what the import seeds and what WotLK publishes: nothing to write."""
    from tests.support_native import Recorder, install

    rec = Recorder()
    install(rec, tmp_path / "srv")
    assert not [s for s in rec.sql_scripts if "SET port" in s], rec.sql_scripts


def test_a_realm_port_that_could_not_be_written_stops_the_install(tmp_path: Path) -> None:
    from yulon.catalog.installer import InstallerError

    with pytest.raises(InstallerError, match="8086"):
        _install_second(tmp_path, failing_sql="SET port")


def test_the_client_is_pointed_at_a_non_standard_auth_port(tmp_path: Path) -> None:
    from yulon import networking

    play = tmp_path / "play"
    (written,) = networking.write_ready_to_play_realmlists(play, "127.0.0.1", auth_port=3725)
    assert written.read_text(encoding="utf-8").splitlines()[0] == "set realmlist 127.0.0.1:3725"
    (again,) = networking.write_ready_to_play_realmlists(play, "127.0.0.1", auth_port=3724)
    assert again.read_text(encoding="utf-8").splitlines()[0] == "set realmlist 127.0.0.1"


def test_the_networking_plan_tells_players_the_port_too() -> None:
    from yulon import networking

    def realmlist(entry: object, mode: str) -> str | None:
        return networking.plan(
            entry,  # type: ignore[arg-type]
            mode,  # type: ignore[arg-type]
            lan_ip="192.168.1.25",
            firewall="ufw",
            steamos=False,
            wsl=False,
            bindings={},
        ).client_realmlist

    wotlk = load_catalog().get("wow-wotlk")
    assert realmlist(second_ac_entry(), "loopback") == "127.0.0.1:3725"
    assert realmlist(second_ac_entry(), "lan") == "192.168.1.25:3725"
    assert realmlist(wotlk, "loopback") == "127.0.0.1"
    assert realmlist(wotlk, "lan") == "192.168.1.25"


def test_every_realmlist_writer_call_passes_its_entrys_auth_port() -> None:
    """A required keyword stops a caller forgetting the port; this stops one hard-coding it."""
    import ast

    root = Path(__file__).resolve().parents[1] / "yulon"
    found = 0
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in ("write_ready_to_play_realmlists", "write_client_realmlist"):
                continue
            found += 1
            port = next((k.value for k in node.keywords if k.arg == "auth_port"), None)
            assert port is not None and ast.unparse(port).endswith(
                "entry.ports.auth"
            ), f"{path.name}:{node.lineno}"
    assert found == 3


# -- P1b: the AzerothCore tab works on the server it was opened for -----------


def test_the_wotlk_package_spec_is_the_catalog_entrys() -> None:
    from yulon.controller_wow_wotlk import docker_ctl

    assert docker_ctl.SPEC == load_catalog().get("wow-wotlk").container_spec()


def test_module_sql_runs_the_importer_of_the_server_it_was_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yulon import docker
    from yulon.controller_wow_wotlk import modules

    seen: list[docker.ContainerSpec] = []

    def fake(spec: docker.ContainerSpec, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        seen.append(spec)
        return docker.AttachedRun(0, ("done",))

    monkeypatch.setattr(docker, "apply_module_sql", fake)
    modules.apply_module_sql(tmp_path, spec=second_ac_entry().container_spec(), ledger=None)
    assert [s.import_service for s in seen] == [SECOND_CONTAINERS["db_import"]]
    assert seen[0].db == SECOND_CONTAINERS["db"]


def test_the_tab_for_a_second_server_binds_every_seam_to_its_own_containers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Built through the real AzerothCore factory, then pressed where Docker is reached."""
    from yulon import docker
    from yulon.ui import controller_view

    seen: list[docker.ContainerSpec] = []

    def fake(spec: docker.ContainerSpec, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        seen.append(spec)
        return docker.AttachedRun(0, ("done",))

    monkeypatch.setattr(docker, "apply_module_sql", fake)
    entry = second_ac_entry(manifests_from="wow-wotlk")
    services = controller_view._for_wotlk(entry, tmp_path, None, None)
    assert services.controller.spec == entry.container_spec()
    assert services.module_sql is not None
    services.module_sql(lambda _line: None)
    assert [s.import_service for s in seen] == [SECOND_CONTAINERS["db_import"]]


def test_an_entry_with_its_own_manifest_tree_is_not_given_wotlks() -> None:
    """The package's module seams read `manifests/wow-wotlk/`; another tree is refused."""
    from yulon.ui import controller_view

    with pytest.raises(controller_view.UnsupportedGameError, match="manifests"):
        controller_view._for_wotlk(second_ac_entry(), Path("/nonexistent"), None, None)


def test_manifests_from_names_the_tree_an_entry_reads() -> None:
    assert load_catalog().get("wow-wotlk").manifest_game() == "wow-wotlk"
    assert second_ac_entry().manifest_game() == "wow-second-ac"
    assert second_ac_entry(manifests_from="wow-wotlk").manifest_game() == "wow-wotlk"


def test_no_wotlk_bound_default_is_left_to_the_azerothcore_factory() -> None:
    """Every call `_for_wotlk` makes into the WotLK package passes what its default would bind.

    A parameter whose default is WotLK's container, spec or manifest tree is
    right for WotLK and wrong for every other server built by the same factory,
    silently: `apply_module_sql()` ran `ac-db-import` for any server until T552.
    """
    import ast
    import inspect
    import textwrap

    from yulon.controller_wow_wotlk import accounts, console, docker_ctl, maintenance, modules
    from yulon.ui import controller_view

    bound = {
        id(docker_ctl.SPEC),
        id(docker_ctl.SPEC.db),
        id(docker_ctl.SPEC.world),
        id(docker_ctl.SPEC.auth),
    }
    packages = {
        "wotlk_modules": modules,
        "wotlk_console": console,
        "wotlk_maintenance": maintenance,
        "wotlk_accounts": accounts,
    }
    tree = ast.parse(textwrap.dedent(inspect.getsource(controller_view._for_wotlk)))
    checked = 0
    problems: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Name) and owner.id in packages):
            continue
        target = getattr(packages[owner.id], node.func.attr)
        if not callable(target) or inspect.isclass(target):
            continue
        checked += 1
        body = ast.parse(textwrap.dedent(inspect.getsource(target))).body[0]
        assert isinstance(body, ast.FunctionDef)
        for inner in body.body:
            for ref in ast.walk(inner):
                if isinstance(ref, ast.Attribute) and ast.unparse(ref).startswith(
                    "docker_ctl.SPEC"
                ):
                    problems.append(f"{owner.id}.{node.func.attr} reads {ast.unparse(ref)}")
        passed = {k.arg for k in node.keywords}
        params = list(inspect.signature(target).parameters.values())
        for i, param in enumerate(params):
            if param.default is inspect.Parameter.empty or i < len(node.args):
                continue
            wotlk_bound = id(param.default) in bound or param.default == modules.GAME
            if wotlk_bound and param.name not in passed:
                problems.append(f"{owner.id}.{node.func.attr}({param.name}=)")
    assert checked >= 10
    assert not problems, problems


# -- review round 1: the realm port survives a repair, and a resume starts the DB first --


def test_a_resumed_install_starts_the_database_before_the_port_statement(tmp_path: Path) -> None:
    """`start-db` is never recorded, so every resume runs it before `up` (Codex review, P2)."""
    _first, rec = _install_second(tmp_path)
    calls = rec.calls  # type: ignore[attr-defined]
    calls.clear()
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller
    from yulon.catalog.installer import InstallOptions

    engine = AzerothCoreInstaller(
        second_ac_entry(),
        installers_root=TEMPLATES,
        import_probe=rec.probe,  # type: ignore[attr-defined]
        reset_unfinished=rec.reset,  # type: ignore[attr-defined]
        seams=rec.seams(),  # type: ignore[attr-defined]
    )
    list(engine.run(InstallOptions(server_dir=tmp_path / "srv")))
    scripts = rec.sql_scripts  # type: ignore[attr-defined]
    assert scripts.count(PORT_SQL) == 2
    assert "start-db" in calls and calls.index("start-db") < calls.index("sql"), calls


def _guarded_services(
    monkeypatch: pytest.MonkeyPatch, entry: object, run_statement: object, row: str = "8086\n"
) -> object:
    from yulon import apply, docker
    from yulon.ui import controller_view

    started: list[str] = []
    monkeypatch.setattr(apply.DockerSql, "run_statement", run_statement)
    monkeypatch.setattr(apply.DockerSql, "query", lambda self, db, statement: row)
    monkeypatch.setattr(
        docker, "start_database", lambda spec, *_a, **_k: started.append(spec.db) or True
    )
    services = controller_view._for_wotlk(
        entry, Path("/nonexistent/srv"), None, None  # type: ignore[arg-type]
    )
    services.started_dbs = started  # type: ignore[attr-defined]
    return services


def test_before_the_servers_start_the_realm_gets_its_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repair re-runs the importer, which seeds 8085 (Codex adversarial, round 1)."""
    ran: list[tuple[str, str]] = []
    services = _guarded_services(
        monkeypatch,
        second_ac_entry(manifests_from="wow-wotlk"),
        lambda self, db, statement: ran.append((db, statement)),
    )
    controller = services.controller  # type: ignore[attr-defined]
    assert controller.start_guard is None
    assert controller.before_servers() is None
    assert ran == [("auth", PORT_SQL)]
    assert services.started_dbs == [SECOND_CONTAINERS["db"]]  # type: ignore[attr-defined]


@pytest.mark.parametrize("row", ["8085\n", "", "NULL\n", "8086\n8086\n"])
def test_a_row_that_does_not_read_back_the_port_refuses(
    monkeypatch: pytest.MonkeyPatch, row: str
) -> None:
    """An UPDATE that matched nothing exits 0 too (Codex adversarial, round 3)."""
    services = _guarded_services(
        monkeypatch, second_ac_entry(manifests_from="wow-wotlk"), lambda *_a: None, row=row
    )
    reason = services.controller.before_servers()  # type: ignore[attr-defined]
    assert reason is not None and "8086" in reason


def test_a_port_that_could_not_be_set_refuses_the_start_and_the_next_one_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed statement is retried at the next start (Codex adversarial, round 2)."""
    from yulon import apply, controller

    answers: list[Exception | None] = [apply.ApplyError("ERROR 2002: cannot connect"), None]
    ran: list[str] = []

    def flaky(self: object, db: str, statement: str) -> None:
        ran.append(statement)
        failure = answers.pop(0)
        if failure is not None:
            raise failure

    services = _guarded_services(monkeypatch, second_ac_entry(manifests_from="wow-wotlk"), flaky)
    ctl = services.controller  # type: ignore[attr-defined]
    monkeypatch.setattr(ctl, "refuse_start", lambda: None)
    monkeypatch.setattr(ctl, "refuse_a_missing_database", lambda: None)
    with pytest.raises(controller.StartRefused, match="8086"):
        ctl.refuse_before_a_stop()
    ctl.refuse_before_a_stop()
    assert ran == [PORT_SQL, PORT_SQL]


def test_the_step_runs_after_every_refusal_and_before_the_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After the port check and the missing-database check, so it never brings up a database
    Docker lost and never starts one for a start that is refused (Codex review, round 2)."""
    from yulon import controller, docker

    order: list[str] = []
    ctl = controller.Controller(
        second_ac_entry().container_spec(),
        tmp_path,
        before_servers=lambda: order.append("before_servers") or None,
    )
    monkeypatch.setattr(ctl, "refuse_start", lambda: order.append("refuse_start"))
    monkeypatch.setattr(ctl, "port_conflicts", lambda: order.append("ports") or [])
    monkeypatch.setattr(ctl, "refuse_a_missing_database", lambda: order.append("database"))
    monkeypatch.setattr(ctl, "_put_back_the_zone_file", lambda: None)
    monkeypatch.setattr(docker, "start_staged", lambda *_a, **_k: order.append("servers"))
    ctl.start()
    assert order == ["refuse_start", "ports", "database", "before_servers", "servers"]

    refused = controller.Controller(
        second_ac_entry().container_spec(), tmp_path, before_servers=lambda: "no port"
    )
    monkeypatch.setattr(refused, "refuse_start", lambda: None)
    monkeypatch.setattr(refused, "port_conflicts", lambda: [])
    monkeypatch.setattr(refused, "refuse_a_missing_database", lambda: None)
    order.clear()
    with pytest.raises(controller.StartRefused, match="no port"):
        refused.start()
    assert "servers" not in order


def test_a_wotlk_start_has_no_port_step() -> None:
    from yulon.ui import controller_view

    services = controller_view._for_wotlk(
        load_catalog().get("wow-wotlk"), Path("/nonexistent/srv"), None, None
    )
    assert services.controller.before_servers is None


@pytest.mark.parametrize("row", ["8085\n", "", "8086\n8086\n"])
def test_an_install_whose_realm_row_does_not_read_back_the_port_stops(
    tmp_path: Path, row: str
) -> None:
    """An UPDATE that matched no row exits 0 too (Codex review and adversarial, round 4)."""
    from yulon.catalog.installer import InstallerError

    with pytest.raises(InstallerError, match="8086"):
        _install_second(tmp_path, realm_port=row)


def test_an_azerothcore_entry_with_no_container_prefix_is_refused() -> None:
    """The renderer refuses an empty prefix; the catalog now refuses it first (Codex, r4)."""
    bare = {
        "db": "database",
        "auth": "authserver",
        "world": "worldserver",
        "db_import": "db-import",
        "client_data": "client-data-init",
    }
    with pytest.raises(ValidationError, match="prefix"):
        second_ac_entry(containers=bare)
