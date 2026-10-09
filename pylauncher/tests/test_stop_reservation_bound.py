"""T607 item 3: a Stop's reservation is bounded all the way, so a wedged Docker never hangs Stop.

T568 gave a Stop 5 s (`_STOP_RESERVE_TIMEOUT`) for Docker to make its reservation, and then
went ahead without one. That 5 s covered only the wait for the container to come up. It did
not cover the per-name lock another thread of this process holds through its own reservation
(up to a minute to make, 15 + 5 + 10 s to release), nor the image look-ups (one `docker
inspect` per container, 5 s each) that run before the container is even asked for. The whole
take is one budget now, and releasing a Stop's reservation does not wait on a slow daemon.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from yulon import docker, platform

SPEC = docker.ContainerSpec(db="ac-database", auth="ac-auth", world="ac-world", ports=(1,))
IMAGE = "yulon.local/wotlk-server:native"
ran: list[str] = []


@docker._a_lifecycle_command
def stop_staged(
    spec: docker.ContainerSpec, server_dir: Path, *, wsl_distro: str | None = None
) -> None:
    ran.append("stop")


@docker._a_lifecycle_command
def start_staged(
    spec: docker.ContainerSpec, server_dir: Path, *, wsl_distro: str | None = None
) -> None:
    ran.append("start")


@pytest.fixture(autouse=True)
def _nothing_ran() -> None:
    ran.clear()


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text(IMAGE + "\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


@pytest.fixture
def server(tmp_path: Path) -> Path:
    folder = tmp_path / "server"
    folder.mkdir()
    return folder


def _within(limit: float, run: Any) -> float:
    """Run `run()` on a thread; its elapsed seconds, failing if it is still going at `limit`."""
    began = time.monotonic()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(limit)
    assert not worker.is_alive(), f"still running after {limit} s: the Stop is hung"
    return time.monotonic() - began


def test_a_stop_does_not_wait_past_its_budget_for_the_per_name_lock(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another thread of this process is mid-reservation (or releasing, 10 s of waiting)."""
    monkeypatch.setattr(docker, "_STOP_RESERVE_TIMEOUT", 0.4)
    ident = docker.folder_id(server)
    lock = docker._reserve_lock(docker.SERVER_CLAIM_PREFIX + str(ident))
    with lock:  # the other thread's take or release, in progress
        took = _within(3.0, lambda: stop_staged(SPEC, server))
    assert ran == ["stop"], "Stop must go ahead unreserved"
    assert took < 2.0


def test_a_start_still_waits_for_the_lock_and_does_not_run_unreserved(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ident = docker.folder_id(server)
    lock = docker._reserve_lock(docker.SERVER_CLAIM_PREFIX + str(ident))
    lock.acquire()
    worker = threading.Thread(target=lambda: start_staged(SPEC, server), daemon=True)
    worker.start()
    worker.join(1.0)
    still_waiting = worker.is_alive()
    lock.release()
    worker.join(10.0)
    assert still_waiting, "a Start gave up on the lock"
    assert ran == ["start"] and not worker.is_alive()


def test_a_stop_does_not_wait_on_slow_image_lookups(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three `docker inspect`s of 3 s each (5 s allowed each) ran before the 5 s budget began."""
    (fake_docker / "inspect-hangs").write_text("", encoding="utf-8")
    monkeypatch.setattr(docker, "_STOP_RESERVE_TIMEOUT", 0.5)
    took = _within(8.0, lambda: stop_staged(SPEC, server))
    assert ran == ["stop"]
    assert took < 2.5, f"the Stop took {took:.1f} s"


def test_a_stops_reservation_is_released_without_the_long_waits(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release of a reservation waits 15 s for its CLI, 5 s to remove it and 10 s for it
    to be gone: right for a press, not for the end of a Stop on a daemon that is not answering."""
    waits: dict[str, list[float | None]] = {"cli": [], "gone": []}
    real_end, real_gone = docker._end_claim_cli, docker._wait_gone

    def end(proc: Any, *, wait: float = docker._CLAIM_RELEASE_TIMEOUT) -> None:
        waits["cli"].append(wait)
        real_end(proc, wait=wait)

    def gone(*args: Any, limit: float | None = None, **kwargs: Any) -> bool:
        waits["gone"].append(limit)
        return real_gone(*args, limit=limit, **kwargs)

    monkeypatch.setattr(docker, "_end_claim_cli", end)
    monkeypatch.setattr(docker, "_wait_gone", gone)
    stop_staged(SPEC, server)
    stopped = {k: list(v) for k, v in waits.items()}
    waits["cli"].clear()
    waits["gone"].clear()
    start_staged(SPEC, server)
    assert stopped["cli"] and all(w is not None and w <= 3.0 for w in stopped["cli"]), stopped
    assert stopped["gone"] and all(w is not None and w <= 3.0 for w in stopped["gone"]), stopped
    # A Start's release keeps the full waits: only a Stop is cut short.
    assert waits["cli"] == [docker._CLAIM_RELEASE_TIMEOUT]
    assert waits["gone"] == [None]


def test_waiting_for_a_container_to_go_never_outlasts_its_limit_on_a_silent_daemon(
    fake_docker: Path, server: Path
) -> None:
    """One look is no longer than what is left of the limit (here 3 s of silence, 0.4 s left)."""
    (fake_docker / "inspect-hangs").write_text("", encoding="utf-8")
    began = time.monotonic()
    docker._wait_gone("yulon-busy-none", None, 0.4)
    assert time.monotonic() - began < 1.5
