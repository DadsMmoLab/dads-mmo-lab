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
      *--running*)
        if [ -e "$state/up" ] || [ -e "$state/prelisted" ]; then echo "{distro}"; fi
        exit 0 ;;
      *) echo "{distro}"; exit 0 ;;
    esac ;;
esac
shift 4   # -d <distro> --exec docker
echo "$1" >> "$state/asked.log"
if [ -e "$state/no-docker-cli" ]; then
  echo "wsl: execvpe(docker) failed: No such file or directory" >&2
  exit 1
fi
if [ -e "$state/hang-once" ] && [ ! -e "$state/hung" ]; then
  touch "$state/hung"
  exec sleep 100
fi
if [ -e "$state/live-shape" ] && [ ! -e "$state/shaped" ]; then
  # Measured live (unc-probe-output.txt): one `docker version` blocked ~33 s, printed the
  # systemd line, then exited 0 with the version.
  touch "$state/shaped" "$state/up" "$state/ready"
  sleep {cold_s}
  echo "wsl: Failed to start the systemd user session for 'pk'. See journalctl." >&2
  exec "{docker}" "$@"
fi
if [ -e "$state/desktop-stub" ]; then
  echo "The command 'docker' could not be found in this WSL 2 distro." >&2
  echo "We recommend to activate the WSL integration in Docker Desktop settings." >&2
  exit 127
fi
if [ -e "$state/no-permission" ]; then
  echo "permission denied while trying to connect to the Docker daemon socket at" \\
       "unix:///var/run/docker.sock: Get \\"http://%2Fvar%2Frun%2Fdocker.sock/v1.47/version\\":" \\
       "dial unix /var/run/docker.sock: connect: permission denied" >&2
  exit 1
fi
if [ ! -e "$state/up" ]; then
  sleep {cold_s}
  touch "$state/up"
  if [ ! -e "$state/dockerd-never" ]; then
    (sleep {dock_s}; touch "$state/ready") </dev/null >/dev/null 2>&1 &
  fi
  if [ -e "$state/noisy-boot" ]; then
    echo "mkdir: cannot create directory '/run/user/1000': permission denied" >&2
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


def test_a_running_distro_is_asked_once_and_not_waited_for(cold_distro: Path, server: Path) -> None:
    (cold_distro / "up").write_text("")
    (cold_distro / "ready").write_text("")
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
        pass
    assert _probes(cold_distro) == 1  # one quick question, no wait


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


def _refused_at_once(state: Path, server: Path) -> str:
    began = time.monotonic()
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
            pytest.fail("went ahead")
    assert refused.value.moot  # the press meets it itself; a hold must not refuse (T622)
    assert _probes(state) == 1  # one probe: waiting cannot fix it
    assert time.monotonic() - began < 8
    return str(refused.value)


def test_a_docker_socket_that_refuses_the_user_stops_the_wait_and_says_permission(
    cold_distro: Path, server: Path
) -> None:
    (cold_distro / "no-permission").write_text("")
    said = _refused_at_once(cold_distro, server)
    assert "permission" in said and "docker group" in said
    assert "wait a moment" not in said
    assert said.endswith("Nothing was changed.")


def test_a_docker_that_needs_docker_desktop_stops_the_wait_and_names_desktop(
    cold_distro: Path, server: Path
) -> None:
    (cold_distro / "desktop-stub").write_text("")
    said = _refused_at_once(cold_distro, server)
    assert "Docker Desktop" in said and "WSL integration" in said
    assert "wait a moment" not in said
    assert said.endswith("Nothing was changed.")


def test_a_distro_without_a_docker_program_says_to_install_docker(
    cold_distro: Path, server: Path
) -> None:
    (cold_distro / "no-docker-cli").write_text("")
    said = _refused_at_once(cold_distro, server)
    assert "no Docker program" in said and "wait a moment" not in said


def test_a_docker_that_never_comes_up_says_try_again(
    cold_distro: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (cold_distro / "dockerd-never").write_text("")
    monkeypatch.setattr(wsl, "BOOT_TIMEOUT", 6.0)
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
            pytest.fail("went ahead")
    assert "did not answer in time" in str(refused.value)
    assert "try again" in str(refused.value)


def test_a_distro_listed_running_whose_docker_is_still_starting_is_waited_for(
    cold_distro: Path, server: Path
) -> None:
    """Live (2026-10-12, T775 round 3): opening Yu'lon reads the server folder over the WSL share,
    which boots the distro, so by the press WSL lists it RUNNING while systemd and dockerd are
    still coming up. Gating the wait on "stopped" skipped exactly this case."""
    (cold_distro / "prelisted").write_text("")
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO) as held:
        assert held.name.startswith(docker.SERVER_CLAIM_PREFIX)
    assert _probes(cold_distro) >= 3


def test_a_warm_distro_costs_one_quick_probe(cold_distro: Path, server: Path) -> None:
    (cold_distro / "up").write_text("")
    (cold_distro / "ready").write_text("")
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
        pass
    assert _probes(cold_distro) == 1


def test_one_probe_that_hangs_does_not_use_up_the_whole_wait(
    cold_distro: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (cold_distro / "hang-once").write_text("")
    monkeypatch.setattr(wsl, "_PROBE_SECONDS", 4.0)
    monkeypatch.setattr(wsl, "BOOT_TIMEOUT", 30.0)
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
        pass
    assert (cold_distro / "hung").exists()


SPEC = docker.ContainerSpec(db="x-db", auth="x-auth", world="x-world", ports=(1,))


def test_a_probe_that_prints_the_systemd_line_and_exits_zero_counts_as_up(
    cold_distro: Path, server: Path
) -> None:
    (cold_distro / "prelisted").write_text("")
    (cold_distro / "live-shape").write_text("")
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
        pass
    assert _probes(cold_distro) == 1
    assert wsl._PROBE_SECONDS >= 45  # the measured call blocked ~33 s


@pytest.mark.parametrize("cause", ["no-docker-cli", "desktop-stub", "no-permission"])
def test_a_distro_that_cannot_run_docker_still_lets_an_uninstall_hold_go_ahead(
    cold_distro: Path, server: Path, cause: str
) -> None:
    """T622: a broken install stays removable. `docker.server_hold` is what Uninstall, Backup and
    maintenance take; a permanent failure there is `moot` (nothing can race), not a refusal."""
    (cold_distro / cause).write_text("")
    began = time.monotonic()
    ran = False
    with docker.server_hold(server, "Uninstall", spec=SPEC, wsl_distro=DISTRO):
        ran = True
    assert ran and _probes(cold_distro) == 1
    assert time.monotonic() - began < 8


def test_a_daemon_stopped_on_purpose_in_a_running_distro_lets_a_hold_go_ahead(
    cold_distro: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listed running, no boot sign (no systemd line), docker.sock never there: not a boot."""
    (cold_distro / "up").write_text("")
    monkeypatch.setattr(wsl, "BOOT_TIMEOUT", 5.0)
    with docker.server_hold(server, "Uninstall", spec=SPEC, wsl_distro=DISTRO):
        pass
    assert _probes(cold_distro) >= 2


def test_a_boot_that_was_seen_and_never_finishes_still_refuses_a_hold(
    cold_distro: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (cold_distro / "dockerd-never").write_text("")
    (cold_distro / "prelisted").write_text("")
    monkeypatch.setattr(wsl, "BOOT_TIMEOUT", 8.0)
    with pytest.raises(docker.ServerReservationUnavailable):
        with docker.server_hold(server, "Uninstall", spec=SPEC, wsl_distro=DISTRO):
            pytest.fail("went ahead")


def test_boot_in_a_boot_output_that_only_mentions_permission_denied_keeps_waiting(
    cold_distro: Path, server: Path
) -> None:
    """Narrow wording: only docker's own socket sentence ends the wait."""
    (cold_distro / "noisy-boot").write_text("")
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro=DISTRO):
        pass
    assert _probes(cold_distro) >= 2
