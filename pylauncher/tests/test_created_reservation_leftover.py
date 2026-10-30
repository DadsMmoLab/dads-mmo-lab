"""T831: a press clears a reservation a slow Stop left Created instead of refusing it.

A Stop goes ahead without a reservation after five seconds, so when the daemon finishes
the make later the container stays Created with no CLI to start it; the next take removes
it by id and goes ahead.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support_fake_docker import calls as fake_calls
from tests.support_fake_docker import containers as fake_containers
from tests.test_server_claim import IMAGE, _name, fake_docker, server  # noqa: F401
from yulon import docker


def _left_created(state: Path, name: str, *, owner: str, pid: str, host: str) -> None:
    """A reservation Docker made after its press gave up: Created, never started (T831)."""
    (state / "containers").mkdir(exist_ok=True)
    (state / "containers" / name).write_text("created", encoding="utf-8")
    (state / "labels").mkdir(exist_ok=True)
    (state / "labels" / name).write_text(
        "\n".join(
            [
                f"{docker.OWNER_LABEL}={owner}",
                f"{docker.CLAIM_LABEL}=abandoned",
                f"{docker.PRESS_LABEL}=Stop the server",
                f"{docker.WHO_LABEL}=pk@here (Linux)",
                f"{docker.PID_LABEL}={pid}",
                f"{docker.HOST_LABEL}={host}",
            ]
        ),
        encoding="utf-8",
    )


def _dead_pid() -> str:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return str(done.pid)


def _labels_of(state: Path, name: str) -> dict[str, str]:
    text = (state / "labels" / name).read_text(encoding="utf-8")
    return dict(line.split("=", 1) for line in text.splitlines())


def test_a_created_leftover_of_a_dead_process_of_ours_is_removed_and_the_start_goes_ahead(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    name = _name(server)
    _left_created(
        fake_docker, name, owner=docker.owner_id(), pid=_dead_pid(), host=socket.gethostname()
    )
    with docker.server_claim(server, press="Start", images=[IMAGE]) as held:
        assert held.name == name
        assert name in fake_containers(fake_docker)
        assert _labels_of(fake_docker, name)[docker.PID_LABEL] == str(os.getpid())
    assert f"rm -f {name}-id" in fake_calls(fake_docker)


def test_a_created_leftover_of_this_process_is_removed_when_it_is_not_taking_it(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    """The GUI case: the leftover's pid is this live process, which holds nothing (T831)."""
    name = _name(server)
    _left_created(
        fake_docker,
        name,
        owner=docker.owner_id(),
        pid=str(os.getpid()),
        host=socket.gethostname(),
    )
    with docker.server_claim(server, press="Start", images=[IMAGE]) as held:
        assert held.name == name
        assert name in fake_containers(fake_docker)
    assert f"rm -f {name}-id" in fake_calls(fake_docker)


def test_a_created_one_of_another_live_process_is_never_removed(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    """Mutation this catches: dropping the `live_here() is False` condition."""
    name = _name(server)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        _left_created(
            fake_docker,
            name,
            owner=docker.owner_id(),
            pid=str(sleeper.pid),
            host=socket.gethostname(),
        )
        with pytest.raises(docker.ServerReserved):
            with docker.server_claim(server, press="Start", images=[IMAGE]):
                pytest.fail("went ahead on another live process's coming-up reservation")
        assert name in fake_containers(fake_docker)
        assert not [line for line in fake_calls(fake_docker) if line.startswith("rm -f")]
    finally:
        sleeper.kill()
        sleeper.wait()


def test_a_created_one_of_another_config_folder_is_never_removed(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    """Mutation this catches: dropping the owner check."""
    name = _name(server)
    _left_created(
        fake_docker, name, owner="someone-else", pid=_dead_pid(), host=socket.gethostname()
    )
    with pytest.raises(docker.ServerReserved):
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead on another Yu'lon's container")
    assert name in fake_containers(fake_docker)


def test_a_created_one_from_another_computer_is_never_removed(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    """Mutation this catches: dropping the host check."""
    name = _name(server)
    _left_created(fake_docker, name, owner=docker.owner_id(), pid=_dead_pid(), host="ELSEWHERE")
    with pytest.raises(docker.ServerReserved):
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead on another computer's container")
    assert name in fake_containers(fake_docker)


def test_a_created_one_with_no_pid_label_is_never_removed(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    """Mutation this catches: dropping the pid-is-digits check."""
    name = _name(server)
    _left_created(fake_docker, name, owner=docker.owner_id(), pid="", host=socket.gethostname())
    with pytest.raises(docker.ServerReserved):
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead on an unlabelled container")
    assert name in fake_containers(fake_docker)


def test_a_leftover_that_will_not_go_is_refused_as_before(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    """Mutation this catches: dropping `cleared_a_leftover` (loops/extra calls)."""
    name = _name(server)
    _left_created(
        fake_docker, name, owner=docker.owner_id(), pid=_dead_pid(), host=socket.gethostname()
    )
    (fake_docker / "refuse-rm").write_text("", encoding="utf-8")
    with pytest.raises(docker.ServerReserved) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead")
    assert str(refused.value).endswith(f"docker rm -f {name}")
    assert sum(1 for line in fake_calls(fake_docker) if line.startswith("rm -f")) == 1


def test_a_stop_clears_it_too_within_its_budget(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(docker, "_STOP_RESERVE_TIMEOUT", 2.0)
    name = _name(server)
    _left_created(
        fake_docker, name, owner=docker.owner_id(), pid=_dead_pid(), host=socket.gethostname()
    )
    with docker.server_claim(
        server, press="Stop", images=[IMAGE], up_timeout=docker._STOP_RESERVE_TIMEOUT
    ) as held:
        assert held.name == name
        assert name in fake_containers(fake_docker)
