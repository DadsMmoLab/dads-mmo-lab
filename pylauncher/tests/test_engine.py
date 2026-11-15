"""Tests for `yulon.engine` — the T887 Podman seam (phase 1, dark).

Podman is chosen ONLY when the `YULON_ENGINE=podman` developer switch is set on
real Linux (not WSL). Without it every seam below answers exactly as Docker did.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from yulon.catalog.catalog import CatalogEntry

from tests.support_native import Recorder
from yulon import docker, engine, platform
from yulon.catalog import composegen, native, preflight

# -- resolve() ---------------------------------------------------------------


def test_resolve_is_docker_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No switch, no Podman: the ordinary machine is unchanged."""
    monkeypatch.delenv("YULON_ENGINE", raising=False)
    engine._reset_for_tests()
    try:
        assert engine.resolve(sys_platform="linux", wsl=False) == "docker"
    finally:
        engine._reset_for_tests()


def test_resolve_is_podman_with_the_switch_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer switch on real Linux selects Podman."""
    monkeypatch.setenv("YULON_ENGINE", "podman")
    engine._reset_for_tests()
    try:
        assert engine.resolve(sys_platform="linux", wsl=False) == "podman"
        assert engine.is_podman()
    finally:
        engine._reset_for_tests()


@pytest.mark.parametrize("sys_platform", ["win32", "darwin"])
def test_resolve_stays_docker_off_linux_with_the_switch(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str
) -> None:
    """Windows and macOS never take the Podman branch, switch or no switch."""
    monkeypatch.setenv("YULON_ENGINE", "podman")
    engine._reset_for_tests()
    try:
        assert engine.resolve(sys_platform=sys_platform, wsl=False) == "docker"
    finally:
        engine._reset_for_tests()


def test_resolve_stays_docker_in_wsl_with_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """WSL is Linux-shaped but not a Podman host: Docker Desktop serves it."""
    monkeypatch.setenv("YULON_ENGINE", "podman")
    engine._reset_for_tests()
    try:
        assert engine.resolve(sys_platform="linux", wsl=True) == "docker"
    finally:
        engine._reset_for_tests()


def test_resolve_ignores_other_switch_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything but exactly `podman` is Docker."""
    monkeypatch.setenv("YULON_ENGINE", "PODMAN")
    engine._reset_for_tests()
    try:
        assert engine.resolve(sys_platform="linux", wsl=False) == "docker"
    finally:
        engine._reset_for_tests()


def test_resolve_names_docker_for_a_foreign_docker_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A user-set `DOCKER_HOST` at real Docker wins over the Podman switch."""
    _podman(monkeypatch, tmp_path)
    try:
        with caplog.at_level("INFO", logger="yulon.engine"):
            monkeypatch.setenv("DOCKER_HOST", "tcp://custom:2375")
            assert engine.resolve(sys_platform="linux", wsl=False) == "docker"
        assert "DOCKER_HOST" in caplog.text and "tcp://custom:2375" in caplog.text
    finally:
        engine._reset_for_tests()


def test_resolve_stays_podman_when_docker_host_is_the_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unset, empty, or the socket itself: the switch still selects Podman."""
    for host in (None, "", f"unix://{tmp_path}/podman/podman.sock"):
        _podman(monkeypatch, tmp_path)
        try:
            if host is None:
                monkeypatch.delenv("DOCKER_HOST", raising=False)
            else:
                monkeypatch.setenv("DOCKER_HOST", host)
            assert engine.resolve(sys_platform="linux", wsl=False) == "podman"
        finally:
            engine._reset_for_tests()


# -- podman_socket() ---------------------------------------------------------


def test_podman_socket_uses_xdg_runtime_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`$XDG_RUNTIME_DIR/podman/podman.sock` when the variable is set."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run" / "1000"))
    assert engine.podman_socket() == tmp_path / "run" / "1000" / "podman" / "podman.sock"


def test_podman_socket_falls_back_to_uid_run_user() -> None:
    """No `XDG_RUNTIME_DIR`: `/run/user/<uid>/podman/podman.sock`."""
    assert engine.podman_socket(uid=1000) == Path("/run/user/1000/podman/podman.sock")


# -- apply_docker_host() -----------------------------------------------------


def _podman(monkeypatch: pytest.MonkeyPatch, runtime: Path) -> None:
    """Select Podman with a faked runtime dir, resetting the cache around it."""
    monkeypatch.setenv("YULON_ENGINE", "podman")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(engine, "_on_supported_platform", lambda *_a, **_k: True)
    engine._reset_for_tests()


def test_user_docker_host_is_never_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `DOCKER_HOST` the user set themselves is left alone under Podman."""
    _podman(monkeypatch, tmp_path)
    try:
        monkeypatch.setenv("DOCKER_HOST", "tcp://custom:2375")
        engine.apply_docker_host()
        assert os.environ["DOCKER_HOST"] == "tcp://custom:2375"
    finally:
        engine._reset_for_tests()


def test_apply_docker_host_sets_the_socket_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Under Podman with no `DOCKER_HOST`, the CLI is pointed at the socket."""
    _podman(monkeypatch, tmp_path)
    try:
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        engine.apply_docker_host()
        assert os.environ["DOCKER_HOST"] == f"unix://{tmp_path}/podman/podman.sock"
    finally:
        engine._reset_for_tests()


def test_apply_docker_host_does_nothing_on_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the switch no variable is touched, set or unset."""
    monkeypatch.delenv("YULON_ENGINE", raising=False)
    engine._reset_for_tests()
    try:
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        engine.apply_docker_host()
        assert "DOCKER_HOST" not in os.environ
    finally:
        engine._reset_for_tests()


# -- platform.docker_programs() ----------------------------------------------


def test_docker_programs_includes_the_client_dir_under_podman(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The downloaded client comes first when it is there; PATH keeps the fallback."""
    _podman(monkeypatch, tmp_path)
    try:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "docker").write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(engine, "client_dir", lambda _home=None: bindir)
        monkeypatch.setattr(platform, "detect", lambda: "linux")
        assert platform.docker_programs() == (str(bindir / "docker"), "docker")
    finally:
        engine._reset_for_tests()


def test_docker_programs_is_unchanged_under_podman_without_the_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No downloaded client yet: exactly today's answer, no ghost path."""
    _podman(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(engine, "client_dir", lambda _home=None: tmp_path / "missing")
        monkeypatch.setattr(platform, "detect", lambda: "linux")
        assert platform.docker_programs() == ("docker",)
    finally:
        engine._reset_for_tests()


def test_docker_programs_is_unchanged_without_the_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker today: `("docker",)` on Linux, client dir never asked."""
    monkeypatch.delenv("YULON_ENGINE", raising=False)
    engine._reset_for_tests()
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    assert platform.docker_programs() == ("docker",)


# -- platform.container_user_args() ------------------------------------------


def test_container_user_args_empty_under_podman(monkeypatch: pytest.MonkeyPatch) -> None:
    """Podman runs the image as root; `--user` would map binds into a subuid."""
    _podman(monkeypatch, Path("/nonexistent"))
    try:
        assert platform.container_user_args(platform_id=lambda: "linux") == []
    finally:
        engine._reset_for_tests()


def test_container_user_args_linux_answer_unchanged_without_the_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker on Linux still gets the uid:gid pair."""
    monkeypatch.delenv("YULON_ENGINE", raising=False)
    engine._reset_for_tests()
    assert platform.container_user_args(platform_id=lambda: "linux") == [
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]


# -- composegen.container_user() ----------------------------------------------


def _wotlk_entry() -> CatalogEntry:
    from yulon.catalog.catalog import load_catalog

    return load_catalog().get("wow-wotlk")


def test_composegen_renders_root_user_under_podman(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Podman renders the Windows branch's shape: `user: "0:0"`."""
    _podman(monkeypatch, tmp_path)
    try:
        assert composegen.container_user(platform_id=lambda: "linux") == 'user: "0:0"'
    finally:
        engine._reset_for_tests()


def test_composegen_renders_the_comment_without_the_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker on Linux is unchanged: the comment, and no `0:0`."""
    monkeypatch.delenv("YULON_ENGINE", raising=False)
    engine._reset_for_tests()
    assert "0:0" not in composegen.container_user(platform_id=lambda: "linux")


def test_composegen_render_puts_root_on_the_env_dist_writers_under_podman(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The full render carries the key on exactly the three bound-etc writers."""
    from tests.test_composegen import (
        TEMPLATES,
        WRITES_TO_BOUND_ETC,
        _services_with_user_key,
    )

    _podman(monkeypatch, tmp_path)
    try:
        plan = composegen.render(
            _wotlk_entry(),
            tmp_path,
            templates_root=TEMPLATES,
            platform_id=lambda: "linux",
        )
        assert _services_with_user_key(plan.base) == WRITES_TO_BOUND_ETC
    finally:
        engine._reset_for_tests()


# -- platform.docker_containerd_root() + preflight gather ---------------------


def _canned(info: dict[str, object]) -> Callable[[], dict[str, object] | None]:
    def run(*_args: object, **_kwargs: object) -> dict[str, object] | None:
        return info

    return run


_CONTAINERD_INFO: dict[str, object] = {
    "OperatingSystem": "Ubuntu 24.04.1 LTS",
    "DockerRootDir": "/var/lib/docker",
    "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]],
}


def test_containerd_root_is_none_under_podman(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T884's check must not fire under Podman: no containerd store to measure."""
    _podman(monkeypatch, tmp_path)
    try:
        (tmp_path / "containerd").mkdir()
        monkeypatch.setattr(platform, "_CONTAINERD_ROOT", tmp_path / "containerd")
        monkeypatch.setattr(platform, "docker_info", _canned(_CONTAINERD_INFO))
        assert platform.docker_containerd_root() is None
    finally:
        engine._reset_for_tests()


def test_preflight_skips_containerd_under_podman(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`gather()` never asks about the containerd store under Podman."""
    from yulon import platform as platform_module

    _podman(monkeypatch, tmp_path)
    try:
        calls: list[str] = []

        def never() -> Path | None:
            calls.append("asked")
            raise AssertionError("gather asked about containerd under Podman")

        server = tmp_path / "server"
        server.mkdir()
        got = preflight.gather(
            _wotlk_entry(),
            server,
            platform_id=lambda: "linux",
            docker_ready=lambda: True,
            compose_ready=lambda: True,
            compose_version=lambda: None,
            vm_resources=lambda: None,
            data_root=lambda: tmp_path / "moved",
            containerd_root=never,
            disk_free=lambda _p: 135 * preflight.GIB,
            dir_problem=lambda _p: None,
            bind_mount_ok=lambda _p: True,
            port_conflicts=lambda: [],
            port_holders=lambda _ports: docker.PortHolders(),
            bind_port=lambda host, port: platform_module.PortBind(host, port, "free", ""),
            probe_port=lambda host, port: platform_module.PortProbe(host, port, "unknown", ""),
        )
        assert (got.containerd_root, got.containerd_free) == (None, None)
        assert calls == [], "fake records the call; it must stay uncalled"
    finally:
        engine._reset_for_tests()


# -- docker._port_refusal ------------------------------------------------------


_ROOTLESSPORT = (
    "Error response from daemon: rootlessport listen tcp 0.0.0.0:3724: "
    "bind: address already in use\n"
)


def test_port_refusal_reads_the_rootlessport_text() -> None:
    """The spike's exact Q6 string gives the same player sentence Docker's does."""
    assert docker._port_refusal(_ROOTLESSPORT) == docker.blocked_port_sentence(
        3724, "in_use", windows=False
    )


def test_port_refusal_still_reads_docker_wording() -> None:
    """Today's three patterns keep working beside the new one."""
    assert "3724" in (
        docker._port_refusal(
            "Error response from daemon: Bind for 0.0.0.0:3724 failed: "
            "port is already allocated\n"
        )
        or ""
    )


# -- docker.daemon_identity ----------------------------------------------------


def test_daemon_identity_is_empty_under_podman(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yulon import runner

    def never(
        cmd: list[str], cwd: object = None, timeout: object = None
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"docker ran under Podman: {cmd}")

    _podman(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(runner, "run", never)
        assert docker.daemon_identity() == ""
    finally:
        engine._reset_for_tests()


# -- install record engine -----------------------------------------------------


def test_install_record_engine_defaults_to_docker(tmp_path: Path) -> None:
    """A record without the key reads as docker: every pre-T887 install."""
    (tmp_path / native.STATE_FILE).write_text(
        '{"version": 1, "game_id": "wow-wotlk", "install_id": "abc", '
        '"family": "azerothcore", "completed": []}\n',
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=())
    assert state is not None
    assert (state.engine or "docker") == "docker"


def test_install_record_round_trips_the_engine(tmp_path: Path) -> None:
    """`write_state` persists the engine; `read_state` parses it back."""
    import json

    native.write_state(
        tmp_path,
        native.InstallState(
            game_id="wow-wotlk", install_id="abc", family="azerothcore", engine="podman"
        ),
    )
    written = (tmp_path / native.STATE_FILE).read_text(encoding="utf-8")
    assert json.loads(written)["engine"] == "podman"
    state = native.read_state(tmp_path, valid=())
    assert state is not None
    assert state.engine == "podman"
    native.write_state(
        tmp_path,
        native.InstallState(
            game_id="wow-wotlk", install_id="abc", family="azerothcore", engine="docker"
        ),
    )
    written = json.loads((tmp_path / native.STATE_FILE).read_text(encoding="utf-8"))
    assert "engine" not in written
    reread = native.read_state(tmp_path, valid=())
    assert reread is not None
    assert (reread.engine or "docker") == "docker"


def _fresh_state(recorder: Recorder, server_dir: Path) -> native.InstallState:
    """The guard's fresh record for `server_dir`, through the real family engine."""
    from tests.support_native import ENTRY
    from yulon import resources
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller

    installer = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=recorder.probe,
        reset_unfinished=recorder.reset,
        seams=recorder.seams(),
    )
    return installer._claim_folder(server_dir)


def test_fresh_install_records_podman_under_the_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard's fresh record is written from `engine.resolve()` at install."""
    from tests.support_native import Recorder

    _podman(monkeypatch, tmp_path)
    try:
        server_dir = tmp_path / "wow"
        server_dir.mkdir()
        assert _fresh_state(Recorder(), server_dir).engine == "podman"
    finally:
        engine._reset_for_tests()


def test_fresh_install_records_docker_without_the_switch(tmp_path: Path) -> None:
    """Without the switch the fresh record says docker, byte for byte as before."""
    from tests.support_native import Recorder

    engine._reset_for_tests()
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    assert _fresh_state(Recorder(), server_dir).engine == "docker"


def test_a_docker_record_reopened_under_podman_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A docker/absent record met under Podman refuses before any command or write."""
    from tests.support_native import ENTRY, Recorder
    from yulon import resources
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller
    from yulon.catalog.installer import InstallerError

    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    installer = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=Recorder().probe,
        reset_unfinished=Recorder().reset,
        seams=Recorder().seams(),
    )
    opened = installer._claim_folder(server_dir)
    assert (opened.engine or "docker") == "docker"
    native.write_state(server_dir, opened)
    _podman(monkeypatch, tmp_path)
    try:
        with pytest.raises(InstallerError, match="set up with Docker.*now using Podman"):
            installer._claim_folder(server_dir)
    finally:
        engine._reset_for_tests()


def test_a_podman_record_reopened_without_the_switch_is_refused(tmp_path: Path) -> None:
    """A podman record met without the switch refuses: switch back to Podman."""
    from tests.support_native import ENTRY, Recorder
    from yulon import resources
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller
    from yulon.catalog.installer import InstallerError

    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    installer = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=Recorder().probe,
        reset_unfinished=Recorder().reset,
        seams=Recorder().seams(),
    )
    native.write_state(
        server_dir,
        native.InstallState(
            game_id="wow-wotlk",
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="azerothcore",
            engine="podman",
        ),
    )
    engine._reset_for_tests()
    with pytest.raises(InstallerError, match="set up with Podman.*Switch back to Podman"):
        installer._claim_folder(server_dir)


def test_a_docker_record_without_the_switch_proceeds(tmp_path: Path) -> None:
    """Without the switch a docker/absent record never refuses: no behaviour change."""
    from tests.support_native import Recorder

    engine._reset_for_tests()
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    assert (_fresh_state(Recorder(), server_dir).engine or "docker") == "docker"
