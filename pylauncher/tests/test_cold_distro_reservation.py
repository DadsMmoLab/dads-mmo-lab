"""T775: the first Start on a stopped WSL distro waits for the distro to boot.

`wsl.exe -d X --exec docker ...` boots a stopped distro, which takes longer than the
5 s a reservation question gets (`docker._CLAIM_ASK_TIMEOUT`). The fake `wsl` here acts
cold: until a boot (`--exec true`) has finished, every other command takes longer than the
bound and fails; after it, `wsl ... docker` is the fake docker CLI. The distro's state is
read from the marker the boot leaves, through the real `wsl.distro_state()` listing.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from yulon import docker, platform, wsl
from yulon.controller import Controller

IMAGE = "yulon.local/wotlk-server:native"
DISTRO = "Ubuntu"

FAKE_WSL = """#!/bin/sh
# argv: -d <distro> --exec docker args...   (or --list --running / --list --quiet)
# Measured on yulon-win11 (2026-10-12): the first command into the stopped distro takes
# {cold_s} s and ends "Failed to start the systemd user session"; the distro is then listed
# running, but docker.sock does not exist until dockerd is up, {dock_s} s later.
state="{state}"
case "$1" in
  --list|-l)
    case "$*" in
      *--running*) [ -e "$state/up" ] && echo "{distro}" ; exit 0 ;;
      *) echo "{distro}"; exit 0 ;;
    esac ;;
esac
shift 4   # -d <distro> --exec docker
echo "$1" >> "$state/asked.log"
if [ -e "$state/no-docker-cli" ]; then
  echo "wsl: execvpe(docker) failed: No such file or directory" >&2
  exit 1
fi
if [ ! -e "$state/up" ]; then
  sleep {cold_s}
  touch "$state/up"
  if [ ! -e "$state/dockerd-never" ]; then
    (sleep {dock_s}; touch "$state/ready") </dev/null >/dev/null 2>&1 &
  fi
  echo "wsl: Failed to start the systemd user session for 'pk'." >&2
  exit 1
fi
if [ ! -e "$state/ready" ]; then
  echo "failed to connect to the docker API at unix:///var/run/docker.sock;" \\
       "check if the daemon is running: dial unix /var/run/docker.sock:" \\
       "connect: no such file or directory" >&2
  exit 1
fi
exec "{docker}" "$@"
"""


@pytest.fixture
def cold_distro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    fake = tmp_path / "cold-wsl"
    fake.write_text(
        FAKE_WSL.format(state=state, distro=DISTRO, docker=cli, cold_s=3, dock_s=3),
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setattr(platform, "_which", lambda name, path=None: str(fake))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    monkeypatch.setattr(docker, "_CLAIM_ASK_TIMEOUT", 1.0)
    monkeypatch.setattr(wsl, "_wsl_listing", _listing(fake))
    yield state
    end_fake_containers(state)


def _listing(fake: Path) -> Callable[..., tuple[str, ...] | None]:

    def listing(*args: str) -> tuple[str, ...] | None:
        done = subprocess.run(
            [str(fake), "--list", *(args or ("--quiet",))], capture_output=True, text=True
        )
        return tuple(done.stdout.split())

    return listing


@pytest.fixture
def server(tmp_path: Path) -> Path:
    folder = tmp_path / "server"
    folder.mkdir()
    return folder


def _asked(state: Path) -> list[str]:
    log = state / "asked.log"
    return log.read_text().split() if log.exists() else []


def _probes(state: Path) -> int:
    return _asked(state).count("version")


def test_a_start_on_a_stopped_distro_waits_until_its_docker_answers_and_reserves(
    cold_distro: Path, server: Path
) -> None:
    """Three stages (live log): no answer, user session failing, docker.sock not there yet."""
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO) as held:
        assert held.name.startswith(docker.SERVER_CLAIM_PREFIX)
    assert _probes(cold_distro) >= 3


def test_a_cold_start_does_not_go_ahead_without_a_reservation(
    cold_distro: Path, server: Path
) -> None:
    """Through `lifecycle` -> `_in_flight`: a `moot` refusal there means "run without one"."""
    (cold_distro / "images-listed").write_text(IMAGE + "\n", encoding="utf-8")
    with docker.lifecycle(server, press="Start", wsl_distro=DISTRO):
        assert (cold_distro / "ready").exists()
        assert any(
            box.name.startswith(docker.SERVER_CLAIM_PREFIX) for box in _containers(cold_distro)
        )


def _containers(state: Path) -> list[Path]:
    return list((state / "containers").iterdir())


def test_a_running_distro_is_not_probed(cold_distro: Path, server: Path) -> None:
    (cold_distro / "up").write_text("")
    (cold_distro / "ready").write_text("")
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
        pass
    assert _probes(cold_distro) == 0


def test_a_docker_that_never_comes_up_is_a_refusal_not_a_go_ahead(
    cold_distro: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (cold_distro / "dockerd-never").write_text("")
    monkeypatch.setattr(wsl, "BOOT_TIMEOUT", 8.0)
    began = time.monotonic()
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.lifecycle(server, press="Start", wsl_distro=DISTRO):
            pytest.fail("went ahead")
    assert not refused.value.moot
    assert _probes(cold_distro) >= 3  # retried while the daemon was missing
    assert time.monotonic() - began < 25


def test_a_distro_wsl_does_not_list_is_not_booted_and_fails_fast(
    cold_distro: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wsl, "_wsl_listing", lambda *a: ("Other",))
    began = time.monotonic()
    with pytest.raises(docker.ServerReservationUnavailable):
        with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
            pytest.fail("went ahead")
    assert _probes(cold_distro) == 0
    assert time.monotonic() - began < 8


def test_a_distro_without_a_docker_program_fails_at_once(cold_distro: Path, server: Path) -> None:
    (cold_distro / "no-docker-cli").write_text("")
    began = time.monotonic()
    with pytest.raises(docker.ServerReservationUnavailable):
        with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
            pytest.fail("went ahead")
    assert _probes(cold_distro) == 1
    assert time.monotonic() - began < 8


def test_the_apps_give_up_ends_a_start_waiting_for_a_distro_through_controller_start(
    cold_distro: Path, server: Path
) -> None:
    """`Controller.start()` hands `stop_control.abandon` down to the boot wait (T775)."""
    (cold_distro / "dockerd-never").write_text("")
    ctl = Controller(
        docker.ContainerSpec(db="x-db", auth="x-auth", world="x-world", ports=(1,)),
        server,
        wsl_distro=DISTRO,
    )
    ctl.stop_control = docker.StopControl()
    threading.Timer(1.5, ctl.stop_control.abandon.set).start()
    began = time.monotonic()
    with pytest.raises(docker.ServerReservationUnavailable):
        ctl.start()
    assert time.monotonic() - began < 15  # the wait is 90 s
