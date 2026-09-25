"""T127: the Tortoise Bots tab's "Bot dashboard" switch.

The files half (`yulon.catalog.bot_dashboard`), the Docker half
(`controller_wow_tortoise.botdash`) with Docker replaced at `yulon.docker`'s
functions, the carry-over through the two writers that replace those files, the
Start hook, the update route, and the tab -- with its work held off the GUI
thread.
"""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from tests.conftest import pump_until
from tests.support_native import Recorder
from yulon import docker, resources, useraccounts
from yulon.catalog import bot_dashboard as files
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import cmangos
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.controller_wow_tortoise import botdash
from yulon.controller_wow_tortoise import controller as tortoise_controller
from yulon.ui import controller_view as controller_view_module
from yulon.ui.widgets.job import run_inline

TORTOISE = load_catalog().get("wow-tortoise")
PASSWORD = "Pw" + "q" * 10
"""Built rather than spelled; nothing here should look like a generated secret."""

CONF_TEXT = (
    "[TortoiseBotsConf]\n"
    "TortoiseBots.LogLevel = 1\n"
    "# Observability telemetry UDP emitter.\n"
    "AiPlayerbot.Observability = 0\n"
    "AiPlayerbot.ObservabilityPort = 9195\n"
    "AiPlayerbot.ObservabilityHost = 127.0.0.1\n"
)


def _install(tmp_path: Path, *, label: str = "") -> Path:
    """A Tortoise install's files as the install engine leaves them, and nothing else."""
    server_dir = tmp_path / "tortoise-server"
    server_dir.mkdir(parents=True)
    plan = composegen.render(
        TORTOISE,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password=PASSWORD,
        bind_label=label,
        platform_id=lambda: "linux",
    )
    composegen.write_plan(plan, server_dir)
    conf = server_dir / "etc" / "modules" / "tortoise_bots.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(CONF_TEXT, encoding="utf-8")
    os.chmod(conf, 0o600)
    dockerfile = (
        server_dir / "src/tortoise-wow/modules/TortoiseBots" / botdash.DOCKERFILE_DIR / "Dockerfile"
    )
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    (server_dir / files.DBC_DIR).mkdir(parents=True)
    return server_dir


class _Docker:
    """`yulon.docker`'s functions this feature calls, recorded in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, world_up: bool = True) -> None:
        self.calls: list[str] = []
        self.world_up = world_up
        self.build_code = 0
        self.up_error: str | None = None
        self.ips = iter(["10.0.0.5", "10.0.0.5"])
        monkeypatch.setattr(docker, "build_image", self.build_image)
        monkeypatch.setattr(docker, "compose_up_service", self.compose_up_service)
        monkeypatch.setattr(docker, "compose_remove_service", self.compose_remove_service)
        monkeypatch.setattr(docker, "world_running", self.world_running)
        monkeypatch.setattr(docker, "remove_image", self.remove_image)
        monkeypatch.setattr(docker, "container_ip", self.container_ip)

    def build_image(
        self, context: Path, tag: str, *, sink: Callable[[str], None] | None = None, **_kw: object
    ) -> docker.AttachedRun:
        self.calls.append(f"build {context.name} {tag}")
        if sink is not None:
            sink("#1 building")
        return docker.AttachedRun(self.build_code, ("#1 building",))

    def compose_up_service(
        self, server_dir: Path, service: str, *, force_recreate: bool = False, **_kw: object
    ) -> None:
        self.calls.append(f"up {service}{' --force-recreate' if force_recreate else ''}")
        if self.up_error:
            raise docker.DockerCommandError(self.up_error)

    def compose_remove_service(self, server_dir: Path, service: str, **_kw: object) -> None:
        # The block must still name the service when compose is asked to remove it.
        assert files.block_in(files.base_path(server_dir).read_text(encoding="utf-8"))
        self.calls.append(f"rm {service}")

    def world_running(self, container: str, **_kw: object) -> bool | None:
        return self.world_up

    def remove_image(self, ref: str, **_kw: object) -> str:
        self.calls.append(f"rmi {ref}")
        return ""

    def container_ip(self, container: str, **_kw: object) -> str | None:
        return next(self.ips)


class _Lifecycle:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def stop(self) -> bool:
        self.calls.append("stop")
        return True

    def start(self) -> None:
        self.calls.append("start")


def _switch(server_dir: Path, lifecycle: _Lifecycle | None = None) -> botdash.Dashboard:
    return botdash.Dashboard(TORTOISE, server_dir, lifecycle or _Lifecycle())  # type: ignore[arg-type]


def _conf(server_dir: Path) -> Path:
    return server_dir / "etc" / "modules" / "tortoise_bots.conf"


def _base(server_dir: Path) -> str:
    return (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")


# -- on -------------------------------------------------------------------------


def test_on_writes_the_conf_keys_and_the_service_and_binds_localhost_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    lifecycle = _Lifecycle()
    before_base = _base(server_dir)

    said = list(_switch(server_dir, lifecycle).switch_on(lan=False))

    conf = _conf(server_dir)
    values = files.conf_values(conf.read_text(encoding="utf-8"), tuple(files.conf_keys(TORTOISE)))
    assert values == {
        "AiPlayerbot.Observability": "1",
        # The service NAME: the world is another container on the same network.
        "AiPlayerbot.ObservabilityHost": "tortoise-observability",
        "AiPlayerbot.ObservabilityPort": "9195",
    }
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600, "the conf keeps its own mode"
    backup = conf.with_name(conf.name + files.CONF_BACKUP_SUFFIX)
    assert backup.read_text(encoding="utf-8") == CONF_TEXT
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600, "the backup keeps the mode too"

    base = _base(server_dir)
    block = files.block_in(base)
    assert block is not None
    assert base.replace(block, "") == before_base, "only the block's lines are new"
    assert '"127.0.0.1:8095:8095"' in block and "0.0.0.0:8095" not in block
    assert "container_name: tortoise-observability" in block
    assert "networks: [tortoise-net]" in block
    assert "DB_HOST: tortoise-db" in block
    assert "DB_PASSWORD: ${DB_ROOT_PASSWORD:?" in block
    assert "- ./data/dbc:/dbc:ro\n" in block
    assert "9195:" not in block, "the telemetry port is never published"
    assert PASSWORD not in base, "no password is written into the compose file"
    image = files.image_ref(TORTOISE, server_dir)
    assert f"image: {image}" in block
    env = (server_dir / composegen.DOTENV_FILE).read_text(encoding="utf-8")
    secret = [line for line in env.splitlines() if line.startswith(files.SECRET_VAR + "=")]
    assert len(secret) == 1 and len(secret[0].split("=", 1)[1]) == 64
    assert f"DB_ROOT_PASSWORD={PASSWORD}" in env, "the password line is left as it was"
    assert not [line for line in said if PASSWORD in line or secret[0] in line]

    # The build first (a failure there changes nothing), the daemon before the
    # world restarts (the module resolves the name once, when it loads).
    assert fake.calls == [f"build observability {image}", "up tortoise-observability"]
    assert lifecycle.calls == ["stop", "start"]
    assert files.state(server_dir) == files.State(on=True, lan=False)


def test_the_opt_in_binds_every_interface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server_dir = _install(tmp_path)
    _Docker(monkeypatch)

    list(_switch(server_dir).switch_on(lan=True))

    block = files.block_in(_base(server_dir))
    assert block is not None and '"0.0.0.0:8095:8095"' in block and "127.0.0.1:8095" not in block
    assert files.state(server_dir) == files.State(on=True, lan=True)


def test_on_with_the_server_stopped_starts_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    _Docker(monkeypatch, world_up=False)
    lifecycle = _Lifecycle()

    said = list(_switch(server_dir, lifecycle).switch_on(lan=False))

    assert lifecycle.calls == []
    assert said[-1] == "The server is stopped. The bots start sending when you start it."


def test_a_failed_build_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    fake.build_code = 1
    before = _base(server_dir)

    with pytest.raises(botdash.SwitchError, match="could not be built"):
        list(_switch(server_dir).switch_on(lan=False))

    assert _base(server_dir) == before
    assert _conf(server_dir).read_text(encoding="utf-8") == CONF_TEXT
    assert (
        not (server_dir / composegen.DOTENV_FILE)
        .read_text(encoding="utf-8")
        .count(files.SECRET_VAR)
    )


def test_a_daemon_that_will_not_start_puts_both_files_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Port 8095 taken, say: the switch never ends half on."""
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    fake.up_error = "Bind for 127.0.0.1:8095 failed: port is already allocated"
    before = _base(server_dir)
    lifecycle = _Lifecycle()

    with pytest.raises(botdash.SwitchError, match="port is already allocated"):
        list(_switch(server_dir, lifecycle).switch_on(lan=False))

    assert _base(server_dir) == before
    assert _conf(server_dir).read_text(encoding="utf-8") == CONF_TEXT
    assert stat.S_IMODE(_conf(server_dir).stat().st_mode) == 0o600
    assert not _conf(server_dir).with_name("tortoise_bots.conf.before-dashboard").exists()
    assert lifecycle.calls == [], "no restart for a switch that did not happen"
    assert "rm tortoise-observability" in fake.calls


def test_the_bind_label_is_read_from_the_file_and_a_mix_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path, label=":z")
    _Docker(monkeypatch)
    list(_switch(server_dir).switch_on(lan=False))
    block = files.block_in(_base(server_dir))
    assert block is not None and "- ./data/dbc:/dbc:ro,z\n" in block

    mixed = _install(tmp_path / "mixed", label=":z")
    base = mixed / composegen.BASE_FILE
    base.write_text(
        base.read_text(encoding="utf-8").replace(
            "./etc:/opt/tortoise/etc:z", "./etc:/opt/tortoise/etc", 1
        ),
        encoding="utf-8",
    )
    before = base.read_text(encoding="utf-8")
    with pytest.raises(botdash.SwitchError, match="SELinux"):
        list(_switch(mixed).switch_on(lan=False))
    assert base.read_text(encoding="utf-8") == before


def test_a_compose_file_yulon_did_not_write_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    base = server_dir / composegen.BASE_FILE
    base.write_text("services:\n  mine:\n    image: x\n", encoding="utf-8")
    with pytest.raises(botdash.SwitchError, match="not written by Yu'lon"):
        list(_switch(server_dir).switch_on(lan=False))
    assert fake.calls == []


# -- off ------------------------------------------------------------------------


def test_off_removes_the_service_and_the_conf_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    before = _base(server_dir)
    switch = _switch(server_dir)
    list(switch.switch_on(lan=False))
    fake.calls.clear()

    said = list(switch.switch_off())

    assert _base(server_dir) == before, "the compose file is byte for byte as it was"
    conf = _conf(server_dir)
    assert conf.read_text(encoding="utf-8") == CONF_TEXT, "the conf is byte for byte as it was"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert not conf.with_name(conf.name + files.CONF_BACKUP_SUFFIX).exists()
    image = files.image_ref(TORTOISE, server_dir)
    assert fake.calls == ["rm tortoise-observability", f"rmi {image}"]
    assert said[-1] == "The bot dashboard is off."
    assert files.state(server_dir) == files.State()


def test_off_keeps_every_other_edit_made_to_the_conf_while_it_was_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    _Docker(monkeypatch)
    switch = _switch(server_dir)
    list(switch.switch_on(lan=False))
    conf = _conf(server_dir)
    conf.write_text(
        conf.read_text(encoding="utf-8").replace("LogLevel = 1", "LogLevel = 3"), encoding="utf-8"
    )

    list(switch.switch_off())

    assert conf.read_text(encoding="utf-8") == CONF_TEXT.replace("LogLevel = 1", "LogLevel = 3")


# -- carry-over: the writers that replace these files ----------------------------


def test_a_render_over_an_install_with_the_dashboard_on_carries_the_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """generate-compose on a resume, and any Repair that re-renders the base file."""
    server_dir = _install(tmp_path)
    _Docker(monkeypatch)
    list(_switch(server_dir).switch_on(lan=True))
    on_disk = _base(server_dir)

    plan = composegen.render(
        TORTOISE,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password=PASSWORD,
        platform_id=lambda: "linux",
    )

    assert plan.base == on_disk
    composegen.write_plan(plan, server_dir)
    assert files.state(server_dir) == files.State(on=True, lan=True)


def test_a_render_over_an_install_without_the_dashboard_adds_nothing(tmp_path: Path) -> None:
    server_dir = _install(tmp_path)
    plan = composegen.render(
        TORTOISE,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password=PASSWORD,
        platform_id=lambda: "linux",
    )
    assert files.block_in(plan.base) is None
    assert plan.base == _base(server_dir)


def _conf_stage(server_dir: Path) -> list[str]:
    """The CMaNGOS conf stage over an install whose `etc/` is already there (a resume)."""
    etc = server_dir / cmangos.ETC_DIR
    data = TORTOISE.install.native.cmangos  # type: ignore[union-attr]
    for name in data.conf.files:
        path = etc / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# a conf\n", encoding="utf-8")
    engine = CmangosInstaller(
        TORTOISE,
        installers_root=resources.installers_dir(),
        seams=Recorder().seams(platform_id=lambda: "linux"),
    )
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=TORTOISE.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="cmangos",
            completed=("conf",),
        ),
        cancel=None,
        secrets=native.Secrets(db_password=PASSWORD),
    )
    return list(engine._conf(ctx))


def test_the_conf_stage_keeps_the_dashboard_keys_while_the_switch_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Repair/resume re-runs the conf stage, whose table says Observability = 0."""
    server_dir = _install(tmp_path)
    _Docker(monkeypatch)
    list(_switch(server_dir).switch_on(lan=False))

    _conf_stage(server_dir)

    text = _conf(server_dir).read_text(encoding="utf-8")
    assert files.conf_values(text, tuple(files.conf_keys(TORTOISE))) == {
        "AiPlayerbot.Observability": "1",
        "AiPlayerbot.ObservabilityHost": "tortoise-observability",
        "AiPlayerbot.ObservabilityPort": "9195",
    }


def test_the_conf_stage_still_writes_the_catalogs_zero_when_the_switch_is_off(
    tmp_path: Path,
) -> None:
    server_dir = _install(tmp_path)
    conf = _conf(server_dir)
    conf.write_text(CONF_TEXT.replace("Observability = 0", "Observability = 1"), encoding="utf-8")

    _conf_stage(server_dir)

    assert "AiPlayerbot.Observability = 0\n" in conf.read_text(encoding="utf-8")


def test_the_etc_dir_is_the_conf_stages_own() -> None:
    assert files.ETC_DIR == cmangos.ETC_DIR


def test_the_catalog_names_the_file_that_carries_the_switch() -> None:
    assert files.conf_file(TORTOISE) == "modules/tortoise_bots.conf"
    for other in ("wow-wotlk", "wow-tbc", "wow-vanilla"):
        assert files.conf_file(load_catalog().get(other)) is None, other


# -- the Start hook ----------------------------------------------------------------


def test_the_tortoise_start_brings_the_dashboard_up_first_and_re_asserts_the_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop is `compose stop` (the dashboard too); Start names three services.

    And a writer this branch does not have (Reset to default) may have written
    the catalog's 0 back: the switch is the block, so the start puts the keys
    back before the world reads them.
    """
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    list(_switch(server_dir).switch_on(lan=False))
    _conf(server_dir).write_text(CONF_TEXT, encoding="utf-8")
    fake.calls.clear()
    order: list[str] = []
    monkeypatch.setattr(tortoise_controller.TortoiseController, "port_conflicts", lambda _self: [])
    monkeypatch.setattr(docker, "start_staged", lambda *_a, **_kw: order.append("start_staged"))
    monkeypatch.setattr(
        docker, "compose_up_service", lambda *_a, **_kw: order.append("dashboard up")
    )

    tortoise_controller.controller_for(server_dir).start()

    assert order == ["dashboard up", "start_staged"]
    assert "AiPlayerbot.Observability = 1" in _conf(server_dir).read_text(encoding="utf-8")


def test_the_tortoise_start_leaves_a_switched_off_install_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    order: list[str] = []
    monkeypatch.setattr(tortoise_controller.TortoiseController, "port_conflicts", lambda _self: [])
    monkeypatch.setattr(docker, "start_staged", lambda *_a, **_kw: order.append("start_staged"))
    monkeypatch.setattr(docker, "compose_up_service", lambda *_a, **_kw: order.append("up"))

    tortoise_controller.controller_for(server_dir).start()

    assert order == ["start_staged"]
    assert _conf(server_dir).read_text(encoding="utf-8") == CONF_TEXT


def test_a_dashboard_that_will_not_start_never_stops_the_server_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    _Docker(monkeypatch)
    list(_switch(server_dir).switch_on(lan=False))
    order: list[str] = []

    def refuse(*_a: object, **_kw: object) -> None:
        raise docker.DockerCommandError("port is already allocated")

    monkeypatch.setattr(tortoise_controller.TortoiseController, "port_conflicts", lambda _self: [])
    monkeypatch.setattr(docker, "compose_up_service", refuse)
    monkeypatch.setattr(docker, "start_staged", lambda *_a, **_kw: order.append("start_staged"))

    tortoise_controller.controller_for(server_dir).start()

    assert order == ["start_staged"]


# -- Update the server to latest -----------------------------------------------------


def _update(_cancel: threading.Event | None) -> Iterator[str]:
    yield "engine: updated"


def test_the_update_rebuilds_the_dashboard_and_restarts_only_for_a_new_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    lifecycle = _Lifecycle()
    switch = _switch(server_dir, lifecycle)
    list(switch.switch_on(lan=False))
    fake.calls.clear()
    lifecycle.calls.clear()
    on_disk = (_base(server_dir), _conf(server_dir).read_text(encoding="utf-8"))

    said = list(botdash.after_update(_update, None, dashboard=switch))

    image = files.image_ref(TORTOISE, server_dir)
    assert said[0] == "engine: updated"
    assert fake.calls == [
        f"build observability {image}",
        "up tortoise-observability --force-recreate",
    ]
    assert lifecycle.calls == [], "same address: the world still finds it"
    assert (_base(server_dir), _conf(server_dir).read_text(encoding="utf-8")) == on_disk

    fake.ips = iter(["10.0.0.5", "10.0.0.9"])
    list(botdash.after_update(_update, None, dashboard=switch))
    assert lifecycle.calls == ["stop", "start"], "a new address: the world is restarted"


def test_the_update_does_nothing_more_for_a_switched_off_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    said = list(botdash.after_update(_update, None, dashboard=_switch(server_dir)))
    assert said == ["engine: updated"]
    assert fake.calls == []


def test_the_tortoise_tab_holds_the_switch_and_the_wrapped_update_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real factory: the seam is wired, and the update press is wrapped."""
    from yulon import install_wiring
    from yulon.controller_wow_tortoise import botpool
    from yulon.ui.controller_view import ControllerServices

    class Engine:
        def update_to_latest(self, _options: object, **_kw: object) -> Iterator[str]:
            yield "engine: updated"

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry: Engine())
    monkeypatch.setattr(botpool, "head_sha", lambda _dest: "same")
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    lifecycle: list[str] = []
    monkeypatch.setattr(
        tortoise_controller.TortoiseController, "stop", lambda _self: lifecycle.append("stop")
    )
    monkeypatch.setattr(
        tortoise_controller.TortoiseController, "start", lambda _self: lifecycle.append("start")
    )
    services = ControllerServices.for_entry(TORTOISE, server_dir)
    seam = services.bot_dashboard
    assert isinstance(seam, botdash.Dashboard)
    list(seam.switch_on(lan=False))
    fake.calls.clear()
    route = services.update_to_latest
    assert route is not None

    list(route.press(None))

    assert f"build observability {files.image_ref(TORTOISE, server_dir)}" in fake.calls
    for other in ("wow-wotlk", "wow-tbc", "wow-vanilla"):
        other_services = ControllerServices.for_entry(load_catalog().get(other), tmp_path)
        assert other_services.bot_dashboard is None, other


# -- the GM hint ------------------------------------------------------------------------


def test_the_hint_names_the_accounts_that_can_sign_in() -> None:
    listing = useraccounts.Listing(
        accounts=[
            useraccounts.Account(1, "ALICE", 0),
            useraccounts.Account(2, "BOB", 2),
            useraccounts.Account(3, "CAROL", 3),
        ]
    )
    hint = files.gm_hint(listing)
    assert hint.names == ("BOB (GM 2)", "CAROL (GM 3)")
    assert "BOB (GM 2), CAROL (GM 3)" in hint.text


def test_with_no_gm_account_the_hint_points_at_the_accounts_tabs_grant() -> None:
    hint = files.gm_hint(useraccounts.Listing(accounts=[useraccounts.Account(1, "ALICE", 1)]))
    assert hint.names == ()
    assert "Accounts tab" in hint.text and "Set GM level" in hint.text


def test_an_unreadable_listing_says_so_rather_than_none() -> None:
    hint = files.gm_hint(useraccounts.Listing(problem="the database is down"))
    assert "the database is down" in hint.text and "None of your accounts" not in hint.text


# -- the tab ---------------------------------------------------------------------------------


class _Seam:
    """The tab's seam, recording which methods ran and on which thread."""

    url = files.URL

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.threads: list[threading.Thread] = []
        self.on = False

    def _note(self, name: str) -> None:
        self.calls.append(name)
        self.threads.append(threading.current_thread())

    def state(self) -> files.State:
        self._note("state")
        return files.State(on=self.on)

    def world_running(self) -> bool | None:
        self._note("world_running")
        return True

    def switch_on(self, *, lan: bool, cancel: threading.Event | None = None) -> Iterator[str]:
        self._note(f"switch_on lan={lan}")
        self.on = True
        yield "on"

    def switch_off(self, cancel: threading.Event | None = None) -> Iterator[str]:
        self._note("switch_off")
        self.on = False
        yield "off"

    def restart_world(self, cancel: threading.Event | None = None) -> Iterator[str]:
        self._note("restart_world")
        yield "restarted"


class _Accounts:
    def __init__(self, listing: useraccounts.Listing) -> None:
        self._listing = listing
        self.threads: list[threading.Thread] = []

    def listing(self) -> useraccounts.Listing:
        self.threads.append(threading.current_thread())
        return self._listing

    def set_password(self, account: str, password: str) -> object:
        raise AssertionError("the dashboard never changes an account")

    def set_gm_level(self, account: str, level: int) -> object:
        raise AssertionError("the dashboard never changes an account")


def _view(
    qapp: object,
    tmp_path: Path,
    seam: _Seam,
    accounts: _Accounts | None = None,
    jobs: Callable[..., None] | None = None,
) -> controller_view_module.ControllerView:
    from dataclasses import replace

    from tests.test_controller_view import _Ps, _services

    services = replace(
        _services(_Ps(), tmp_path, []),
        bot_dashboard=seam,
        accounts=accounts,
        bots=_NoBots(),
    )
    return controller_view_module.ControllerView(
        TORTOISE, services, status_poll_ms=0, job_runner=jobs or run_inline
    )


class _NoBots:
    def page(self, *, after: object = None, name_like: str = "") -> object:
        return None


def _wait(view: controller_view_module.ControllerView, qapp: object) -> None:
    """Until the panel's job has ended AND its finish handler has run (which unlocks `_busy`)."""
    log = view.dashboard_log
    assert log is not None
    pump_until(lambda: not log.running and not view._busy, "the dashboard job finished")


def test_the_switch_asks_then_runs_on_in_the_log(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seam = _Seam()
    view = _view(qapp, tmp_path, seam)
    view.refresh_bot_dashboard()
    assert view.dashboard_switch.isEnabled() and not view.dashboard_switch.isChecked()
    asked: list[str] = []

    def no(_parent: object, title: str, text: str, *_a: object) -> object:
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    view.dashboard_switch.click()
    assert "switch_on lan=False" not in seam.calls
    assert not view.dashboard_switch.isChecked(), "a No leaves the switch where it was"
    assert "127.0.0.1" not in asked[0] and files.URL in asked[0] and "restarted" in asked[0]

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    view.dashboard_lan.setChecked(True)
    view.dashboard_switch.click()
    _wait(view, qapp)
    assert "switch_on lan=True" in seam.calls
    assert view.dashboard_switch.isChecked() and view.dashboard_switch.text() == "Bot dashboard: On"
    assert view.open_dashboard_button.isEnabled()
    assert not view.dashboard_lan.isEnabled(), "the network choice is fixed while it is on"


def test_off_offers_the_restart_after_the_switch(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seam = _Seam()
    seam.on = True
    view = _view(qapp, tmp_path, seam)
    view.refresh_bot_dashboard()
    titles: list[str] = []

    def yes(_parent: object, title: str, *_a: object) -> object:
        titles.append(title)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", yes)
    view.dashboard_switch.click()
    _wait(view, qapp)
    _wait(view, qapp)

    assert titles == ["Switch the bot dashboard off?", "Restart the server now?"]
    assert seam.calls.index("switch_off") < seam.calls.index("restart_world")
    assert not view.dashboard_switch.isChecked()


def test_open_says_which_accounts_can_sign_in_then_opens_the_page(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtGui import QDesktopServices

    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    seam = _Seam()
    seam.on = True
    accounts = _Accounts(useraccounts.Listing(accounts=[useraccounts.Account(7, "ME", 0)]))
    view = _view(qapp, tmp_path, seam, accounts)
    view.refresh_bot_dashboard()

    view.open_dashboard_button.click()

    assert opened == ["http://localhost:8095"]
    assert "None of your accounts" in view.dashboard_report.text()


def test_no_press_reads_a_file_or_asks_docker_on_the_gui_thread(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every seam call goes through the job runner: held here, nothing has run."""
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: None)
    held: list[Callable[[], object]] = []

    def hold(work: Callable[[], object], _done: object, _failed: object) -> None:
        held.append(work)

    seam = _Seam()
    accounts = _Accounts(useraccounts.Listing())
    view = _view(qapp, tmp_path, seam, accounts, jobs=hold)
    held.clear()  # what the other tabs ask for at build time (T94's undo look-up) is theirs
    view.refresh_bot_dashboard()
    view.open_bot_dashboard()
    assert seam.calls == [] and accounts.threads == []
    assert len(held) == 2

    # The switch's work runs in the log panel's own thread.
    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    view.dashboard_switch.setEnabled(True)
    view.dashboard_switch.click()
    _wait(view, qapp)
    assert seam.calls == ["switch_on lan=False"]
    assert seam.threads[0] is not threading.main_thread()


def test_the_dashboard_panel_is_joined_on_exit(qapp: object, tmp_path: Path) -> None:
    view = _view(qapp, tmp_path, _Seam())
    assert view.dashboard_log in view.log_panels()


# -- fix wave 1 (cold review + Codex, 2026-09-25) ---------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="the POSIX mode is a no-op on Windows")
@pytest.mark.parametrize("before", [0o664, 0o644, 0o600])
def test_switch_on_leaves_env_owner_only_under_umask_022(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: int
) -> None:
    """Codex high 1. `.env` holds the root password and now the session secret.

    `write_dotenv()` wrote its temp file at the umask and renamed it over the file,
    so the switch WIDENED a 0600 `.env` to 0644 -- and left the 0664 the install
    made (measured on yulon-ubuntu) as it was.
    """
    server_dir = _install(tmp_path)
    env = server_dir / composegen.DOTENV_FILE
    env.chmod(before)
    _Docker(monkeypatch)
    old = os.umask(0o022)
    try:
        list(_switch(server_dir).switch_on(lan=False))
    finally:
        os.umask(old)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert files.SECRET_VAR in env.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="the POSIX mode is a no-op on Windows")
def test_the_private_writer_never_widens_and_keeps_a_stricter_mode(tmp_path: Path) -> None:
    from yulon import platform as yplatform

    new = tmp_path / "new.env"
    old = os.umask(0o000)
    try:
        yplatform.write_private_atomically(new, b"A=1\n")
        strict = tmp_path / "strict.env"
        strict.write_bytes(b"x")
        strict.chmod(0o400)
        yplatform.write_private_atomically(strict, b"A=2\n")
    finally:
        os.umask(old)
    assert stat.S_IMODE(new.stat().st_mode) == 0o600
    assert stat.S_IMODE(strict.stat().st_mode) == 0o400 and strict.read_bytes() == b"A=2\n"
    assert not list(tmp_path.glob("*.yulon-new"))


def _on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, _Docker, botdash.Dashboard]:
    server_dir = _install(tmp_path)
    fake = _Docker(monkeypatch)
    switch = _switch(server_dir)
    list(switch.switch_on(lan=False))
    fake.calls.clear()
    return server_dir, fake, switch


def _assert_still_on(server_dir: Path, fake: _Docker) -> None:
    assert files.state(server_dir) == files.State(on=True, lan=False), "the block is the record"
    assert fake.calls == [], "no container or image was touched"
    conf = _conf(server_dir)
    assert conf.with_name(conf.name + files.CONF_BACKUP_SUFFIX).is_file(), "the retry needs it"


def test_off_with_an_unreadable_conf_leaves_the_switch_on_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex high 2: the conf goes back FIRST, and the block (the record of On) goes last."""
    server_dir, fake, switch = _on(tmp_path, monkeypatch)
    real = files.read_exact
    conf = _conf(server_dir)

    def unreadable(path: Path) -> str:
        if path == conf:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(files, "read_exact", unreadable)
    with pytest.raises(botdash.SwitchError, match="left ON"):
        list(switch.switch_off())
    _assert_still_on(server_dir, fake)

    monkeypatch.setattr(files, "read_exact", real)
    list(switch.switch_off())
    assert files.state(server_dir) == files.State()
    assert conf.read_text(encoding="utf-8") == CONF_TEXT


def test_off_with_an_unwritable_conf_leaves_the_switch_on_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, switch = _on(tmp_path, monkeypatch)
    real = files.write_keeping_mode
    conf = _conf(server_dir)
    on_text = conf.read_text(encoding="utf-8")

    def read_only(path: Path, text: str) -> None:
        if path == conf:
            raise OSError(30, "Read-only file system", str(path))
        real(path, text)

    monkeypatch.setattr(files, "write_keeping_mode", read_only)
    with pytest.raises(botdash.SwitchError, match="left ON"):
        list(switch.switch_off())
    _assert_still_on(server_dir, fake)
    assert conf.read_text(encoding="utf-8") == on_text, "the world's keys still say send"

    monkeypatch.setattr(files, "write_keeping_mode", real)
    list(switch.switch_off())
    assert files.state(server_dir) == files.State()
    assert conf.read_text(encoding="utf-8") == CONF_TEXT


def test_off_whose_container_will_not_go_puts_the_keys_on_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, switch = _on(tmp_path, monkeypatch)
    on_text = _conf(server_dir).read_text(encoding="utf-8")

    def refuse(*_a: object, **_kw: object) -> None:
        raise docker.DockerCommandError("daemon busy")

    monkeypatch.setattr(docker, "compose_remove_service", refuse)
    with pytest.raises(botdash.SwitchError, match="left ON"):
        list(switch.switch_off())
    assert files.state(server_dir).on
    assert _conf(server_dir).read_text(encoding="utf-8") == on_text


def test_the_start_hook_bounds_the_dashboard_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review minor 7: a hung `compose up` must never hold the server's own Start."""
    from yulon import runner

    server_dir, _fake, _switch_ = _on(tmp_path, monkeypatch)
    monkeypatch.setattr(docker, "compose_up_service", _REAL_UP)
    seen: list[tuple[list[str], float | None]] = []

    def hung(cmd: list[str], cwd: Path | None = None, timeout: float | None = None) -> object:
        import subprocess

        seen.append((cmd, timeout))
        return subprocess.CompletedProcess(cmd, 124, "", "timed out")

    monkeypatch.setattr(runner, "run", hung)
    botdash.start_if_on(TORTOISE, server_dir)  # never raises

    ups = [t for cmd, t in seen if cmd[-5:-1] == ["compose", "up", "-d", "--no-deps"]]
    assert ups == [botdash.START_HOOK_TIMEOUT_S]
    assert 0 < botdash.START_HOOK_TIMEOUT_S <= 300


_REAL_UP = docker.compose_up_service


def test_the_daemons_session_secret_is_the_installs_own_never_the_public_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review minor 9, the unit half: the daemon's env names the `.env` secret, and that is
    random -- never the default compiled into the daemon. The live half (a cookie forged with
    the default is refused) is in the gate's curl log."""
    import re as _re

    server_dir, _fake, _switch_ = _on(tmp_path, monkeypatch)
    block = files.block_in(_base(server_dir))
    assert block is not None
    assert f"SESSION_SECRET: ${{{files.SECRET_VAR}:?" in block
    env = (server_dir / composegen.DOTENV_FILE).read_text(encoding="utf-8")
    value = next(
        line.split("=", 1)[1] for line in env.splitlines() if line.startswith(files.SECRET_VAR)
    )
    assert _re.fullmatch(r"[0-9a-f]{64}", value)
    assert value not in ("tortoise-observability-salt-secret", "tortoise-observability-secret-salt")
    # A second install gets a different one; a second switch-on keeps this one.
    assert files.new_secret() != value
    list(_switch(server_dir).switch_off())
    list(_switch(server_dir).switch_on(lan=False))
    assert value in (server_dir / composegen.DOTENV_FILE).read_text(encoding="utf-8")


# -- merge-time: T106's Repair and T94's Reset --------------------------------------------------


def test_an_enforcing_install_with_the_dashboard_reads_labelled_and_repair_keeps_the_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review B4: T106 counted only a trailing `:z`, so `:ro,z` read as MIXED and Repair refused."""
    from tests.test_repair_server_files import engine as repair_engine
    from yulon.catalog.installer import InstallOptions

    server_dir = _install(tmp_path, label=":z")
    (server_dir / ".db_password").write_text(PASSWORD + "\n", encoding="utf-8")
    _Docker(monkeypatch)
    list(_switch(server_dir).switch_on(lan=False))
    text = _base(server_dir)
    assert "- ./data/dbc:/dbc:ro,z" in text
    assert composegen.bind_label_of(text) == ":z"
    assert files.bind_label(text) == ":z"

    eng = repair_engine(TORTOISE, enforcing=True)
    options = InstallOptions(server_dir=server_dir)
    check = eng.base_compose_check(options)
    assert check.state == "current", check
    # And a real repair of a stale file (a comment-free change) keeps the block.
    base = server_dir / composegen.BASE_FILE
    base.write_text(text.replace("restart: unless-stopped\n", "", 1), encoding="utf-8")
    assert eng.base_compose_check(options).state == "stale"
    eng.repair_base_compose(options)
    after = _base(server_dir)
    assert files.block_in(after) == files.block_in(text)
    assert composegen.bind_label_of(after) == ":z"


def test_a_reset_with_the_switch_on_keeps_the_three_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review B5: T94's Reset writes the table back and its banner offers a recreate, not a
    Start, so the Start hook never runs after it. The reset lays the keys over the table."""
    from tests.test_reset_defaults import TEMPLATES, FakeImage, _seams
    from yulon import reset_defaults

    server_dir = _install(tmp_path)
    (server_dir / ".db_password").write_text(PASSWORD + "\n", encoding="utf-8")
    _Docker(monkeypatch)
    list(_switch(server_dir).switch_on(lan=False))
    file = "etc/modules/tortoise_bots.conf"

    reset_defaults.reset(
        TORTOISE, server_dir, [file], seams=_seams(FakeImage(TEMPLATES["wow-tortoise"]))
    )

    text = (server_dir / file).read_text(encoding="utf-8")
    assert files.conf_values(text, tuple(files.conf_keys(TORTOISE))) == files.conf_keys(TORTOISE)
    assert "TortoiseBots.LogLevel = 1" in text, "the rest of the file was reset"

    list(_switch(server_dir).switch_off())
    reset_defaults.reset(
        TORTOISE, server_dir, [file], seams=_seams(FakeImage(TEMPLATES["wow-tortoise"]))
    )
    assert "AiPlayerbot.Observability = 0" in (server_dir / file).read_text(encoding="utf-8")
