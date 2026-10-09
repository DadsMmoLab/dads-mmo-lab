"""T568: a server reservation across processes, made by the Docker daemon.

Two Yu'lons on one daemon (a second OS user, or a Windows and a WSL one on Docker Desktop)
can press Update, Rebuild, Start or Stop on one server folder at once. `docker.server_claim()`
is T543's folder claim made per server: a container `yulon-busy-<folder id>` the daemon
refuses a second of, that dies with its process, and that carries what the refused press
says (which press, who, since when). The docker CLI is `support_fake_docker`'s.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import HANG_BOUND
from tests.support_fake_docker import calls as fake_calls
from tests.support_fake_docker import containers as fake_containers
from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from yulon import docker, platform

IMAGE = "yulon.local/wotlk-server:native"


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    yield state
    end_fake_containers(state)


@pytest.fixture
def server(tmp_path: Path) -> Path:
    folder = tmp_path / "server"
    folder.mkdir()
    return folder


def _name(server: Path) -> str:
    ident = docker.folder_id(server)
    assert ident is not None
    return docker.SERVER_CLAIM_PREFIX + ident


def _wait_for(state: Path, name: str, there: bool) -> None:
    deadline = time.monotonic() + HANG_BOUND
    while (name in fake_containers(state)) != there:
        assert time.monotonic() < deadline, f"{name} {'never came' if there else 'never went'}"
        time.sleep(0.02)


def _runs(state: Path) -> list[str]:
    return [line for line in fake_calls(state) if line.startswith("run ")]


def _labels(state: Path, name: str) -> dict[str, str]:
    text = (state / "labels" / name).read_text(encoding="utf-8")
    return dict(line.split("=", 1) for line in text.splitlines())


def _another_process_holds(
    state: Path,
    name: str,
    *,
    owner: str,
    press: str = "Update the server to latest…",
    pid: str = "999",
    host: str | None = None,
) -> subprocess.Popen[bytes]:
    """Another Yu'lon's reservation: its own docker CLI with stdin held open."""
    proc = subprocess.Popen(
        [
            str(state.parent / "fake-docker"),
            "run", "--rm", "-i", "--name", name,
            "--label", f"{docker.OWNER_LABEL}={owner}",
            "--label", f"{docker.CLAIM_LABEL}=theirs",
            "--label", f"{docker.PRESS_LABEL}={press}",
            "--label", f"{docker.WHO_LABEL}=pk on THEIR-PC (Windows)",
            "--label", f"{docker.PID_LABEL}={pid}",
            *(["--label", f"{docker.HOST_LABEL}={host}"] if host else []),
            "--entrypoint", "sh", IMAGE, "-c", "cat >/dev/null",
        ],
        stdin=subprocess.PIPE,
    )  # fmt: skip
    _wait_for(state, name, there=True)
    return proc


# ------------------------------------------------------------------ the reservation


def test_the_reservation_is_a_named_container_with_the_facts_a_refused_press_reads(
    fake_docker: Path, server: Path
) -> None:
    name = _name(server)
    with docker.server_claim(server, press="Rebuild the server…", images=[IMAGE]) as held:
        assert held.name == name
        assert name in fake_containers(fake_docker)
        labels = _labels(fake_docker, name)
        assert labels[docker.PRESS_LABEL] == "Rebuild the server…"
        assert labels[docker.PID_LABEL] == str(os.getpid())
        assert labels[docker.OWNER_LABEL] == docker.owner_id()
        assert socket.gethostname() in labels[docker.WHO_LABEL]
    _wait_for(fake_docker, name, there=False)


def test_a_nested_take_is_the_same_container_and_the_last_exit_releases_it(
    fake_docker: Path, server: Path
) -> None:
    """Update -> Rebuild: the inner press reuses the outer's reservation (one `docker run`).

    Mutation this catches: a nested take making a second container, which the daemon refuses.
    """
    name = _name(server)
    with docker.server_claim(server, press="Update", images=[IMAGE]) as outer:
        with docker.server_claim(server, press="Rebuild", images=[IMAGE]) as inner:
            assert inner.lost is outer.lost
            assert len(_runs(fake_docker)) == 1
            assert _labels(fake_docker, name)[docker.PRESS_LABEL] == "Update"  # the outer's
        time.sleep(0.2)
        assert name in fake_containers(fake_docker), "the inner exit released the outer's hold"
    _wait_for(fake_docker, name, there=False)
    assert docker.reservation_held_here(server) is False


def test_a_lost_reservation_is_lost_for_every_holder_in_the_process(
    fake_docker: Path, server: Path
) -> None:
    name = _name(server)
    with docker.server_claim(server, press="Update", images=[IMAGE]) as outer:
        with docker.server_claim(server, press="Rebuild", images=[IMAGE]) as inner:
            cli = int((fake_docker / "containers" / name).read_text(encoding="utf-8"))
            os.kill(cli, signal.SIGKILL)  # the reservation's CLI ends from elsewhere
            assert outer.lost.wait(HANG_BOUND)
            assert inner.lost.is_set()
            assert not inner.held()


def test_another_process_holding_refuses_the_press_with_the_holders_facts(
    fake_docker: Path, server: Path
) -> None:
    name = _name(server)
    theirs = _another_process_holds(fake_docker, name, owner="someone-else")
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            with docker.server_claim(
                server, press="Start", images=[IMAGE], label="WoW TBC", this_press="Start"
            ):
                pytest.fail("went ahead under another Yu'lon's reservation")
        said = str(refused.value)
        holder = refused.value.holder
        assert holder.press == "Update the server to latest…"
        assert holder.who == "pk on THEIR-PC (Windows)"
        assert holder.ours is False and holder.here is False
        assert "Another Yu'lon is working on WoW TBC right now" in said, said
        assert "Update the server to latest…" in said and "THEIR-PC" in said, said
        assert "Nothing was changed." in said and "docker rm" not in said, said
        assert name in fake_containers(fake_docker), "another Yu'lon's reservation was removed"
    finally:
        theirs.kill()


def test_a_leftover_of_this_users_own_is_named_with_its_command_and_never_removed(
    fake_docker: Path, server: Path
) -> None:
    name = _name(server)
    theirs = _another_process_holds(fake_docker, name, owner=docker.owner_id())
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            with docker.server_claim(server, press="Start", images=[IMAGE]):
                pytest.fail("went ahead")
        assert refused.value.holder.ours is True and refused.value.holder.here is False
        said = str(refused.value)
        assert said.splitlines()[-1] == f"docker rm -f {name}", said
        assert "earlier run of this Yu'lon" in said, said
        assert name in fake_containers(fake_docker)
    finally:
        theirs.kill()


def test_no_image_to_run_it_from_is_an_unavailable_reservation_in_words(
    fake_docker: Path, server: Path
) -> None:
    (fake_docker / "missing-images").write_text(IMAGE, encoding="utf-8")
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead")
    said = str(refused.value)
    assert "could not reserve" in said and "Nothing was changed." in said, said
    assert "images this server runs from" in said, said
    assert fake_containers(fake_docker) == []


def test_the_image_comes_from_the_databases_container_before_a_built_ref(
    fake_docker: Path, server: Path
) -> None:
    """Docker will not remove an image a container uses, so it is there when the server is.

    Mutation this catches: the chain starting at the built refs (a missing one fails the take).
    """
    (fake_docker / "images").mkdir()
    (fake_docker / "images" / "ac-database").write_text("sha256:dbimage", encoding="utf-8")
    (fake_docker / "missing-images").write_text(IMAGE, encoding="utf-8")
    spec = docker.ContainerSpec(db="ac-database", auth="ac-auth", world="ac-world", ports=(1,))
    with docker.server_claim(server, press="Start", images=[IMAGE], spec=spec):
        pass
    assert (fake_docker / "claim-images.log").read_text(encoding="utf-8").split() == [
        "sha256:dbimage"
    ]


def test_the_chain_falls_through_to_a_built_ref_then_to_a_listed_one(
    fake_docker: Path, server: Path
) -> None:
    (fake_docker / "images-listed").write_text("yulon.local/other:native\n", encoding="utf-8")
    (fake_docker / "missing-images").write_text(IMAGE, encoding="utf-8")
    with docker.server_claim(server, press="Start", images=[IMAGE]):
        pass
    tried = (fake_docker / "claim-images.log").read_text(encoding="utf-8").split()
    assert tried == ["yulon.local/other:native"]


def test_a_distro_servers_reservation_is_made_on_that_distros_docker(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str | None] = []
    real = platform.docker_prefix

    def prefix(wsl_distro: str | None = None, **kw: object) -> tuple[str, ...] | None:
        asked.append(wsl_distro)
        return real(None)

    monkeypatch.setattr(platform, "docker_prefix", prefix)
    with docker.server_claim(server, press="Start", images=[IMAGE], wsl_distro="dml-arch"):
        pass
    assert "dml-arch" in asked


def test_a_folder_that_will_not_take_the_id_file_is_unavailable(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "folder_id", lambda _folder: None)
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead")
    assert docker.FOLDER_ID_FILE in str(refused.value)


def test_the_folder_claim_is_unchanged(fake_docker: Path, server: Path) -> None:
    """T543's argv stays byte for byte: no press/who labels on a data folder's claim."""
    with docker.folder_claim(server, IMAGE):
        (run,) = _runs(fake_docker)
    assert docker.PRESS_LABEL not in run and docker.WHO_LABEL not in run
    assert "--name yulon-claim-" in run


def test_two_threads_of_one_process_share_one_reservation(fake_docker: Path, server: Path) -> None:
    """The reservation is per PROCESS, by design (plan section 3: "in-process rules stay exactly as
    they are"): two worker threads of one Yu'lon share it, and T216's in-process holds
    (`hold_the_server()`, `_IN_FLIGHT`) and each view's busy state keep them apart as before.
    Codex's adversarial review called this a defect; it is the scope of the ticket, recorded
    here so a change to it is made on purpose.
    """
    seen: list[threading.Event] = []
    first_in = threading.Event()
    second_in = threading.Event()

    def first() -> None:
        with docker.server_claim(server, press="p", images=[IMAGE]) as held:
            seen.append(held.lost)
            first_in.set()
            assert second_in.wait(HANG_BOUND)  # held until the second has taken its share

    def second() -> None:
        assert first_in.wait(HANG_BOUND)  # the first is inside: this one is the nested take
        with docker.server_claim(server, press="p", images=[IMAGE]) as held:
            seen.append(held.lost)
            second_in.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(HANG_BOUND)
    assert len(seen) == 2 and seen[0] is seen[1]
    assert len(_runs(fake_docker)) == 1


def test_a_reservation_its_press_lets_go_is_not_lost(fake_docker: Path, server: Path) -> None:
    """The release ends the CLI too, and that is not a loss (the watcher reads `letting_go`).

    Mutation this catches: the release not telling the watcher, so every finished press looks
    like one that was lost from elsewhere and the next one is refused.
    """
    with docker.server_claim(server, press="Update", images=[IMAGE]) as held:
        pass
    time.sleep(0.3)  # the watcher has seen the CLI end by now
    assert not held.lost.is_set()


def test_a_daemon_that_will_not_make_the_reservation_is_moot_not_a_reason_to_refuse(
    fake_docker: Path, server: Path
) -> None:
    """The take itself finding Docker down (an image is there to ask with) is `moot`, like the
    chain finding it down: the command meets that in its own words.

    Mutation this catches: `moot` always False, which put "could not reserve" in front of the
    Docker banner's advice whenever the daemon went away between the chain and the take.
    """
    (fake_docker / "claim-no-daemon").write_text("", encoding="utf-8")
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead")
    assert refused.value.moot is True


def test_an_image_the_daemon_refuses_is_not_moot(fake_docker: Path, server: Path) -> None:
    (fake_docker / "missing-images").write_text(IMAGE, encoding="utf-8")
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead")
    assert refused.value.moot is False


# ------------------------------------------------------------------ a loaded daemon (live, T568)
# On yulon-ubuntu under load 9 the first live run found the container of a finished press
# still there ten seconds later (`rm -f`: "removal ... is already in progress"), so the next
# press of the same Yu'lon was refused as "an earlier run left its reservation", and "Stop
# anyway" removed nothing.


def test_a_finished_press_is_gone_from_docker_before_the_next_can_start(
    fake_docker: Path, server: Path
) -> None:
    """Mutation this catches: the exit not waiting for the container to be gone."""
    (fake_docker / "claim-lingers").write_text("", encoding="utf-8")
    (fake_docker / "rm-lingers").write_text("", encoding="utf-8")
    name = _name(server)
    with docker.server_claim(server, press="Update", images=[IMAGE]):
        pass
    assert name not in fake_containers(fake_docker), "the exit left the container to linger"
    with docker.server_claim(server, press="Rebuild", images=[IMAGE]):  # and nothing refuses it
        assert name in fake_containers(fake_docker)


def test_ending_a_holders_reservation_returns_once_it_is_gone(
    fake_docker: Path, server: Path
) -> None:
    """ "Stop anyway" removes by id, and the Stop that follows must not meet it dying."""
    (fake_docker / "rm-lingers").write_text("", encoding="utf-8")
    name = _name(server)
    theirs = _another_process_holds(fake_docker, name, owner="someone-else")
    try:
        holder = docker.reservation_holder(server)
        assert holder is not None and holder.container
        assert docker.end_reservation(holder) is True
        assert name not in fake_containers(fake_docker)
    finally:
        theirs.kill()


def test_a_holder_that_is_being_removed_is_waited_for_not_refused_for(
    fake_docker: Path, server: Path
) -> None:
    """A container Docker is already removing is nobody's hold: the take waits, then goes.

    Mutation this catches: the dying holder read as a live Yu'lon's (or an earlier run's)
    reservation, which refused a press seconds after the one before it ended.
    """
    (fake_docker / "rm-lingers").write_text("", encoding="utf-8")
    name = _name(server)
    theirs = _another_process_holds(fake_docker, name, owner=docker.owner_id())
    try:
        removing = subprocess.run(
            [str(fake_docker.parent / "fake-docker"), "rm", "-f", name + "-id"], capture_output=True
        )
        assert removing.returncode == 1  # "already in progress": it is `removing` now
        assert (fake_docker / "containers" / name).read_text(encoding="utf-8") == "removing"
        with docker.server_claim(server, press="Start", images=[IMAGE]) as held:
            assert held.name == name
    finally:
        theirs.kill()


def test_a_holder_docker_was_slow_to_name_is_asked_again_before_it_is_called_unknown(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 1 s look that decides "whose is it" fails twice on a loaded daemon; the holder is then
    read once more with the longer bound before the refusal says Docker would not say.

    Mutation this catches: `known=False` taken at the look's word.
    """
    name = _name(server)
    theirs = _another_process_holds(fake_docker, name, owner="someone-else")
    real = docker._claim_facts
    looks: list[float] = []

    def short_look_fails(
        container: str, timeout: float = docker._CLAIM_ASK_TIMEOUT, **kw: object
    ) -> object:
        looks.append(timeout)
        if timeout == docker._CLAIM_LOOK_TIMEOUT:
            return None
        return real(container, timeout=timeout, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(docker, "_claim_facts", short_look_fails)
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            with docker.server_claim(server, press="Start", images=[IMAGE]):
                pytest.fail("went ahead")
        assert refused.value.holder.known is True
        assert refused.value.holder.press == "Update the server to latest…"
        assert docker._CLAIM_ASK_TIMEOUT in looks
    finally:
        theirs.kill()


# ------------------------------------------------------------------ the folder id (Codex review)


def test_an_id_file_that_is_there_but_holds_no_id_refuses_instead_of_running_unreserved(
    fake_docker: Path, server: Path
) -> None:
    """Two Yu'lons that both fail to read a present-but-bad id would both go ahead: refused.

    Mutation this catches: `moot=True` for every folder with no id to be had.
    """
    (server / docker.FOLDER_ID_FILE).write_text("not an id", encoding="ascii")
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead")
    assert refused.value.moot is False
    assert docker.FOLDER_ID_FILE in str(refused.value) and "delete it" in str(refused.value)


def test_a_folder_that_cannot_be_written_to_make_an_id_is_moot(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    def refuse(*_args: object, **_kw: object) -> object:
        raise PermissionError("read-only folder")

    monkeypatch.setattr(tempfile, "mkstemp", refuse)
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE]):
            pytest.fail("went ahead")
    assert refused.value.moot is True
    assert not (server / docker.FOLDER_ID_FILE).exists()


# ------------------------------------------------------------------ cold review of T568


def test_a_daemon_that_hangs_refuses_it_is_neither_moot_nor_no_image(
    fake_docker: Path, server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timed-out look cannot tell whether another Yu'lon holds the server: refused, plainly.

    Not "no image: press Rebuild" (cold review), and not moot (Opus adversarial review): under
    load the look times out before the `docker run` that would have been refused, and running
    unreserved then works beside the holder.
    Mutations this catches: the timeout counted as an absent image; the timeout counted as moot.
    """
    (fake_docker / "inspect-hangs").write_text("", encoding="utf-8")
    monkeypatch.setattr(docker, "_CLAIM_ASK_TIMEOUT", 0.3)
    spec = docker.ContainerSpec(db="d", auth="a", world="w", ports=(1,))
    with pytest.raises(docker.ServerReservationUnavailable) as refused:
        with docker.server_claim(server, press="Start", images=[IMAGE], spec=spec):
            pytest.fail("went ahead")
    assert refused.value.moot is False
    said = str(refused.value)
    assert "Docker is not answering" in said and "whether another Yu'lon is working" in said, said
    assert "Rebuild" not in said and "Nothing was changed" in said, said


def test_the_wait_for_gone_is_for_its_own_container_not_the_name(
    fake_docker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After another Yu'lon's Stop anyway took the name, the old holder's release must not wait
    out the clock for the NEW holder's container.

    Mutation this catches: `_wait_gone` comparing only the name.
    """
    newer = docker._ClaimFacts("newer-container", "running", "n", "o")
    monkeypatch.setattr(docker, "_claim_facts", lambda *_a, **_kw: newer)
    began = time.monotonic()
    assert docker._wait_gone("yulon-busy-x", None, container="older-container") is True
    assert time.monotonic() - began < 1.0
    assert docker._wait_gone("yulon-busy-x", None, container="newer-container", limit=0.3) is False


# ---------------------------------------------------------------- alive or leftover (Opus review)


def test_a_reservation_carries_the_computer_it_was_made_on(fake_docker: Path, server: Path) -> None:
    with docker.server_claim(server, press="Update", images=[IMAGE]):
        assert _labels(fake_docker, _name(server))[docker.HOST_LABEL] == socket.gethostname()


def test_a_holder_is_live_dead_or_cannot_be_told(fake_docker: Path, server: Path) -> None:
    import sys

    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    here = socket.gethostname()
    base = {"name": "n", "container": "c"}
    assert docker.ServerHolder(**base, host=here, pid=str(os.getpid())).live_here() is True
    assert docker.ServerHolder(**base, host=here, pid=str(done.pid)).live_here() is False
    assert docker.ServerHolder(**base, host="elsewhere", pid=str(os.getpid())).live_here() is None
    assert docker.ServerHolder(**base, host="", pid=str(os.getpid())).live_here() is None
    assert docker.ServerHolder(**base, host=here, pid="").live_here() is None


def test_a_live_process_of_this_users_is_a_working_yulon_not_a_leftover(
    fake_docker: Path, server: Path
) -> None:
    """Same config folder, live pid on this computer: the refusal is "working on", with no
    `docker rm -f` line (a headless run or a second window holds it, Opus review)."""
    name = _name(server)
    theirs = _another_process_holds(
        fake_docker, name, owner=docker.owner_id(), pid=str(os.getpid()), host=socket.gethostname()
    )
    try:
        with pytest.raises(docker.ServerReserved) as refused:
            with docker.server_claim(server, press="Start", images=[IMAGE]):
                pytest.fail("went ahead")
        said = str(refused.value)
        assert "Another Yu'lon is working on" in said and "docker rm" not in said, said
    finally:
        theirs.kill()


def test_a_pid_that_cannot_be_probed_is_unknown_not_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected OSError from the probe is "cannot tell", so "Clear it" is not offered.

    Mutation this catches: `False` for any error (the old `pid_is_alive()` answer).
    """
    import errno

    def broken(_pid: int, _sig: int) -> None:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(os, "kill", broken)
    holder = docker.ServerHolder("n", "c", host=socket.gethostname(), pid="12345", ours=True)
    assert holder.live_here() is None
