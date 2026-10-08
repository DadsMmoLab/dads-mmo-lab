"""T543 + T544: two Yu'lons on one Docker daemon, one server folder.

T544: a running extraction tool another Yu'lon made is told apart from one this Yu'lon
left: the press says another Yu'lon is extracting there and never offers to remove its
container. T543: a press claims the folder with a container named by the folder's id
(T536), so the daemon decides which of two presses at once goes ahead.

The docker CLI is `support_fake_docker`'s.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import HANG_BOUND
from tests.support_fake_docker import calls as fake_calls
from tests.support_fake_docker import containers as fake_containers
from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from yulon import container_end, docker, platform
from yulon.after_stop import stop_took_effect
from yulon.catalog.families.trinitycore import TrinityCoreInstaller
from yulon.catalog.installer import InstallerError

THEIRS = "yulon-extract-aaaaaaaaaaaa"
OURS = "yulon-extract-bbbbbbbbbbbb"
IMAGE = "yulon.local/trinitycore-centurion-server:native"


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The fake CLI as this machine's docker; its state folder. Every container is ended after."""
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    yield state
    end_fake_containers(state)


def _running(state: Path, name: str, labels: list[str] | None) -> None:
    """A running container in the fake daemon, with `labels` (None: made before labels)."""
    (state / "containers" / name).write_text("4242", encoding="utf-8")
    if labels is not None:
        (state / "labels").mkdir(exist_ok=True)
        (state / "labels" / name).write_text("\n".join(labels), encoding="utf-8")


def _press(folder: Path) -> str:
    """What the Re-extract press's own refusal says about `folder`; "" when it goes ahead."""
    me = SimpleNamespace(entry=SimpleNamespace(name="Centurion"))
    try:
        TrinityCoreInstaller._refuse_a_tool_still_writing(me, folder)  # type: ignore[arg-type]
    except InstallerError as exc:
        return str(exc)
    return ""


# ------------------------------------- T544: whose tool it is


def test_another_yulons_tool_is_told_apart_from_one_ours_left(
    fake_docker: Path, tmp_path: Path, real_left_tool_read: None, caplog: pytest.LogCaptureFixture
) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    _running(
        fake_docker,
        THEIRS,
        ["yulon.owner=someone-else", f"{docker.WRITES_LABEL}={docker.folder_id(folder)}"],
    )
    _running(fake_docker, OURS, None)

    with caplog.at_level(logging.INFO, logger="yulon.docker"):
        still = docker.tool_containers_writing_into(folder)

    assert still.others == (THEIRS,)
    assert still.ours == (OURS,)
    assert still and still.names == (THEIRS, OURS)
    about_theirs = [r.getMessage() for r in caplog.records if THEIRS in r.getMessage()]
    assert about_theirs and not any("earlier run" in line for line in about_theirs), about_theirs
    assert any("another Yu'lon" in line for line in about_theirs), about_theirs


def test_nothing_running_is_falsy(
    fake_docker: Path, tmp_path: Path, real_left_tool_read: None
) -> None:
    still = docker.tool_containers_writing_into(tmp_path)
    assert not still and still.names == ()


@pytest.mark.parametrize("desktop", [True, False])
def test_the_press_says_another_yulon_is_extracting_and_offers_no_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, desktop: bool
) -> None:
    monkeypatch.setattr(
        docker, "tool_containers_writing_into", lambda _f: docker.StillWriting(others=(THEIRS,))
    )
    monkeypatch.setattr(container_end, "on_docker_desktop", lambda: desktop)

    said = _press(tmp_path)

    assert said.startswith(
        f"Another Yu'lon on this computer is extracting map data into {tmp_path} right now"
    ), said
    assert "docker rm" not in said and "Containers list" not in said, said
    assert "Wait for it to finish" in said and "Nothing was changed." in said, said


def test_with_both_only_ours_is_named_for_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        docker,
        "tool_containers_writing_into",
        lambda _f: docker.StillWriting(ours=(OURS,), others=(THEIRS,)),
    )
    monkeypatch.setattr(container_end, "on_docker_desktop", lambda: False)

    said = _press(tmp_path)

    assert said.splitlines()[-1] == f"docker rm -f {OURS}", said
    assert THEIRS not in said.splitlines()[-1]
    assert "Another Yu'lon on this computer is extracting" in said, said


# ------------------------------------- T543: a claim the daemon arbitrates


def _claim_name(folder: Path) -> str:
    ident = docker.folder_id(folder)
    assert ident is not None
    return docker.CLAIM_PREFIX + ident


def _other_yulons_claim(cli: Path, name: str) -> subprocess.Popen[bytes]:
    """Another Yu'lon holding `name`: its own docker CLI, its stdin held open."""
    proc = subprocess.Popen(
        [
            str(cli),
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--label",
            "yulon.owner=someone-else",
            "--label",
            f"{docker.CLAIM_LABEL}=theirs",
            IMAGE,
            "sh",
            "-c",
            "cat >/dev/null",
        ],
        stdin=subprocess.PIPE,
    )
    return proc


def _wait_for(state: Path, name: str, there: bool) -> None:
    deadline = time.monotonic() + HANG_BOUND
    while (name in fake_containers(state)) != there:
        assert time.monotonic() < deadline, f"{name} {'never came' if there else 'never went'}"
        time.sleep(0.02)


def test_a_claim_is_held_while_inside_and_gone_after(fake_docker: Path, tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    name = _claim_name(folder)

    with docker.folder_claim(folder, IMAGE) as held:
        assert held
        assert name in fake_containers(fake_docker)
    _wait_for(fake_docker, name, there=False)
    with docker.folder_claim(folder, IMAGE) as held:  # and it can be claimed again
        assert held


def test_another_yulons_claim_refuses_the_press_and_is_not_removed(
    fake_docker: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    name = _claim_name(folder)
    theirs = _other_yulons_claim(fake_docker.parent / "fake-docker", name)
    try:
        _wait_for(fake_docker, name, there=True)
        with pytest.raises(docker.FolderClaimed) as refused:
            with docker.folder_claim(folder, IMAGE):
                pytest.fail("the press went ahead under another Yu'lon's claim")
        assert not refused.value.ours
        assert not [c for c in fake_calls(fake_docker) if c.startswith("rm ")], "theirs removed"
        assert name in fake_containers(fake_docker)
    finally:
        assert theirs.stdin is not None
        theirs.stdin.close()
        theirs.wait(HANG_BOUND)


def test_a_claim_this_process_holds_refuses_a_second_press_of_its_own(
    fake_docker: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    with docker.folder_claim(folder, IMAGE) as held:
        assert held
        with pytest.raises(docker.FolderClaimed) as refused:
            with docker.folder_claim(folder, IMAGE):
                pytest.fail("a second press of this Yu'lon went ahead")
        assert refused.value.ours and refused.value.here


def test_a_claim_an_earlier_run_of_this_yulon_left_is_named_and_never_removed(
    fake_docker: Path, tmp_path: Path
) -> None:
    """Codex adversarial review: a claim in place is never removed by a press, even one
    whose owner label is this Yu'lon's; the press names it and the player decides."""
    folder = tmp_path / "data"
    folder.mkdir()
    name = _claim_name(folder)
    _running(fake_docker, name, [f"yulon.owner={docker.owner_id()}", f"{docker.CLAIM_LABEL}=old"])

    with pytest.raises(docker.FolderClaimed) as refused:
        with docker.folder_claim(folder, IMAGE):
            pytest.fail("the press went ahead under a claim it does not hold")
    assert refused.value.ours and not refused.value.here
    assert not [c for c in fake_calls(fake_docker) if c.startswith("rm ")]
    assert name in fake_containers(fake_docker)


@pytest.mark.parametrize("why", ["refused", "no-cli"])
def test_a_claim_that_cannot_be_made_stops_the_press(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    """Codex adversarial review: no claim, no press -- it fails closed."""
    folder = tmp_path / "data"
    folder.mkdir()
    if why == "refused":
        (fake_docker / "claim-refused").write_text("", encoding="utf-8")
    if why == "no-cli":
        monkeypatch.setattr(platform, "docker_program", lambda: None)

    with pytest.raises(docker.ClaimUnavailable):
        with docker.folder_claim(folder, IMAGE):
            pytest.fail("the press went ahead with no claim")
    assert not [n for n in fake_containers(fake_docker) if n.startswith(docker.CLAIM_PREFIX)]


def test_a_folder_with_no_id_stops_the_press(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex adversarial review, round 2: a path names one folder twice under two
    spellings, so a folder with no id is not claimed by its path; the press stops."""
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setattr(docker, "folder_id", lambda _f: None)

    with pytest.raises(docker.ClaimUnavailable, match=docker.FOLDER_ID_FILE):
        with docker.folder_claim(folder, IMAGE):
            pytest.fail("the press went ahead with no id to claim by")
    assert not [c for c in fake_calls(fake_docker) if c.startswith("run ")]


def test_two_yulons_pressing_at_once_one_goes_ahead(fake_docker: Path, tmp_path: Path) -> None:
    """Codex adversarial review of T536: two processes, two owners, one folder, one moment.
    Each waits for the same go, then claims; exactly one holds, the other is refused."""
    folder = tmp_path / "data"
    folder.mkdir()
    go = tmp_path / "go"
    script = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "from yulon import docker, platform\n"
        "platform.docker_program = lambda: sys.argv[1]\n"
        "while not Path(sys.argv[3]).exists(): time.sleep(0.005)\n"
        "try:\n"
        "    with docker.folder_claim(Path(sys.argv[2]), 'img') as held:\n"
        "        print('held' if held else 'unclaimed', flush=True)\n"
        "        time.sleep(3)\n"
        "except docker.FolderClaimed as e:\n"
        "    print('refused-ours' if e.ours else 'refused', flush=True)\n"
        "except docker.ClaimUnavailable as e:\n"
        "    print('unavailable', e, flush=True)\n"
    )
    procs = []
    for who in ("one", "two"):
        env = {**os.environ, "XDG_DATA_HOME": str(tmp_path / who), "PYTHONPATH": str(Path.cwd())}
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(fake_docker.parent / "fake-docker"),
                    str(folder),
                    str(go),
                ],
                stdout=subprocess.PIPE,
                text=True,
                env=env,
            )
        )
    go.write_text("", encoding="utf-8")
    said = sorted(proc.communicate(timeout=HANG_BOUND)[0].strip() for proc in procs)
    assert said == ["held", "refused"], said


def test_a_claim_that_never_runs_is_never_held(fake_docker: Path, tmp_path: Path) -> None:
    """Codex adversarial review, round 3: a claim created whose command then fails is seen,
    with this press's nonce, before `--rm` takes it. Held means running, not just there."""
    folder = tmp_path / "data"
    folder.mkdir()
    (fake_docker / "claim-dies").write_text("", encoding="utf-8")

    with pytest.raises(docker.ClaimUnavailable):
        with docker.folder_claim(folder, IMAGE):
            pytest.fail("the press went ahead on a claim that never ran")


@pytest.mark.parametrize("dies", [True, False])
def test_the_stand_in_claim_container_is_never_seen_empty(
    fake_docker: Path, tmp_path: Path, dies: bool
) -> None:
    """T567: the stand-in made the container file empty and wrote "created" a moment later,
    and an `inspect` in between read an empty file as "running": a claim that never ran was
    held, once in a while, on CI. The container has a state from the instant it exists.
    """
    if dies:
        (fake_docker / "claim-dies").write_text("", encoding="utf-8")
    for turn in range(25):
        name = f"yulon-claim-{turn:012}"
        box = fake_docker / "containers" / name
        run = subprocess.Popen(
            [platform.docker_program(), "run", "--rm", "-i", "--name", name, "--label", "k=v"]
            + ["--label", "j=w", IMAGE, "cat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        seen = None
        deadline = time.monotonic() + HANG_BOUND
        while seen is None and time.monotonic() < deadline:
            try:
                seen = box.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
        run.kill()
        run.wait()
        assert seen, f"turn {turn}: the container was visible with no state in it ({seen!r})"
        assert seen == "created" if dies else seen.isdigit(), seen


def test_a_stop_while_the_claim_comes_up_ends_the_wait_at_once(
    fake_docker: Path, tmp_path: Path
) -> None:
    """Cold review: Docker may take its time with the claim; a Stop does not wait for it."""
    folder = tmp_path / "data"
    folder.mkdir()
    (fake_docker / "claim-slow").write_text("", encoding="utf-8")
    cancel = threading.Event()
    outcome: list[BaseException] = []

    def press() -> None:
        try:
            with docker.folder_claim(folder, IMAGE, cancel):
                pytest.fail("held a claim that never came up")
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is read
            outcome.append(exc)

    worker = threading.Thread(target=press)
    worker.start()
    time.sleep(0.3)
    stopped = time.monotonic()
    cancel.set()
    worker.join(HANG_BOUND)
    took = time.monotonic() - stopped
    (fake_docker / "claim-slow").unlink()
    assert not worker.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], docker.ClaimStopped), outcome
    assert stop_took_effect(outcome[0])
    assert took < 5.0, f"the Stop waited {took:.1f} s for the claim"


@pytest.mark.parametrize(
    ("flag", "says", "not_says"),
    [
        ("claim-refused", "Rebuild the server", "is Docker running"),
        ("claim-no-daemon", "Docker is not running", "Rebuild"),
    ],
)
def test_a_refused_claim_names_its_real_cause(
    fake_docker: Path, tmp_path: Path, flag: str, says: str, not_says: str
) -> None:
    """Cold review: a missing extraction image is not "is Docker running?"."""
    folder = tmp_path / "data"
    folder.mkdir()
    (fake_docker / flag).write_text("", encoding="utf-8")

    with pytest.raises(docker.ClaimUnavailable) as refused:
        with docker.folder_claim(folder, IMAGE):
            pytest.fail("held a refused claim")
    assert says in str(refused.value) and not_says not in str(refused.value), refused.value


def test_a_claim_whose_owner_docker_will_not_say_is_not_called_another_yulons(
    fake_docker: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    name = _claim_name(folder)
    _running(fake_docker, name, ["yulon.owner=someone-else", f"{docker.CLAIM_LABEL}=theirs"])
    (fake_docker / "no-answer").write_text("", encoding="utf-8")

    with pytest.raises(docker.FolderClaimed) as refused:
        with docker.folder_claim(folder, IMAGE):
            pytest.fail("the press went ahead")
    assert not refused.value.known and not refused.value.ours


def test_a_stop_is_not_held_up_by_a_slow_docker_question(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review of the cold-review folds: each look at the coming-up claim asks Docker,
    and a Docker that does not answer held a Stop for the whole 20 s question."""
    folder = tmp_path / "data"
    folder.mkdir()
    (fake_docker / "claim-slow").write_text("", encoding="utf-8")

    def slow(_name: str, timeout: float = docker._ASK_AGAIN_TIMEOUT) -> None:
        time.sleep(timeout)  # Docker does not answer: the question runs to its bound

    monkeypatch.setattr(docker, "_claim_facts", slow)
    cancel = threading.Event()
    outcome: list[BaseException] = []

    def press() -> None:
        try:
            with docker.folder_claim(folder, IMAGE, cancel):
                pytest.fail("held a claim that never came up")
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is read
            outcome.append(exc)

    worker = threading.Thread(target=press)
    worker.start()
    time.sleep(0.3)
    stopped = time.monotonic()
    cancel.set()
    worker.join(HANG_BOUND)
    took = time.monotonic() - stopped
    (fake_docker / "claim-slow").unlink()
    assert not worker.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], docker.ClaimStopped), outcome
    assert took < 5.0, f"the Stop waited {took:.1f} s on a Docker question"


def test_every_docker_question_a_claim_asks_is_short(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review of the folds: a hung daemon must not hold a Stop, or a press, for a
    20 s question anywhere in the claim -- held, refused by another's, given up on."""
    asked: list[tuple[str, float | None]] = []
    real = docker._docker

    def recorded(argv: list[str], *args: object, **kwargs: object) -> object:
        timeout = kwargs.get("timeout")
        asked.append((argv[0], timeout if isinstance(timeout, float | int) else None))
        return real(argv, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(docker, "_docker", recorded)
    folder = tmp_path / "data"
    folder.mkdir()
    with docker.folder_claim(folder, IMAGE):
        pass
    name = _claim_name(folder)
    _running(fake_docker, name, ["yulon.owner=someone-else", f"{docker.CLAIM_LABEL}=theirs"])
    with pytest.raises(docker.FolderClaimed):
        with docker.folder_claim(folder, IMAGE):
            pass
    assert asked and all(t is not None and t <= 5.0 for _a, t in asked), asked


def test_a_claim_docker_makes_after_a_stop_gave_it_up_is_still_removed(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review of the folds: ending the CLI does not cancel a `run` the daemon already
    has, so the claim can appear after the Stop's one look. It is still removed, by its
    nonce, without holding the Stop up -- or it would block every later press."""
    folder = tmp_path / "data"
    folder.mkdir()
    (fake_docker / "claim-slow").write_text("", encoding="utf-8")
    nonce = "a" * 32
    monkeypatch.setattr(docker.uuid, "uuid4", lambda: SimpleNamespace(hex=nonce))
    cancel = threading.Event()
    looks_after_stop = [0]
    removed: list[str] = []

    def facts(_name: str, timeout: float = 5.0) -> object:
        if not cancel.is_set():
            return None
        looks_after_stop[0] += 1
        if looks_after_stop[0] < 3:
            return None  # not made yet
        if looks_after_stop[0] < 6:  # another press held the name for a moment meanwhile
            return docker._ClaimFacts("theirs-id", "running", "b" * 32, "someone-else")
        return docker._ClaimFacts("late-id", "created", nonce, docker.owner_id())

    monkeypatch.setattr(docker, "_claim_facts", facts)
    monkeypatch.setattr(docker, "_remove_claim", lambda c, timeout=5.0: removed.append(c) is None)
    monkeypatch.setattr(docker, "_CLAIM_SWEEP_POLL", 0.05)
    monkeypatch.setattr(docker, "_CLAIM_SWEEP_SECONDS", 10.0)
    outcome: list[BaseException] = []

    def press() -> None:
        try:
            with docker.folder_claim(folder, IMAGE, cancel):
                pytest.fail("held a claim that never came up")
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is read
            outcome.append(exc)

    worker = threading.Thread(target=press)
    worker.start()
    time.sleep(0.3)
    stopped = time.monotonic()
    cancel.set()
    worker.join(HANG_BOUND)
    took = time.monotonic() - stopped
    (fake_docker / "claim-slow").unlink()
    assert len(outcome) == 1 and isinstance(outcome[0], docker.ClaimStopped), outcome
    assert took < 5.0, took
    deadline = time.monotonic() + HANG_BOUND
    while not removed:
        assert time.monotonic() < deadline, "the late claim was left behind"
        time.sleep(0.02)
    assert removed == ["late-id"]


def test_a_stop_during_the_question_whose_claim_it_is_is_a_stop(
    fake_docker: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    name = _claim_name(folder)
    _running(fake_docker, name, ["yulon.owner=someone-else", f"{docker.CLAIM_LABEL}=theirs"])
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(docker.ClaimStopped):
        docker._claim_in_use(name, IMAGE, cancel, again=True)


def test_a_stop_during_the_look_that_finds_the_claim_running_is_a_stop(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review of the folds: Stop pressed while the look that finds the claim running
    is asked. The press does not go ahead on it; the claim is let go of."""
    folder = tmp_path / "data"
    folder.mkdir()
    cancel = threading.Event()
    real = docker._claim_facts

    def look(name: str, timeout: float = 5.0) -> object:
        facts = real(name, timeout=timeout)
        if facts is not None and facts.status == "running":
            cancel.set()  # pressed while Docker answered
        return facts

    monkeypatch.setattr(docker, "_claim_facts", look)

    with pytest.raises(docker.ClaimStopped):
        with docker.folder_claim(folder, IMAGE, cancel):
            pytest.fail("the press went ahead after Stop")
    deadline = time.monotonic() + HANG_BOUND
    while any(n.startswith(docker.CLAIM_PREFIX) for n in fake_containers(fake_docker)):
        assert time.monotonic() < deadline, "the claim was kept after the Stop"
        time.sleep(0.02)


def test_a_late_claim_whose_first_removal_fails_is_swept_again(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review of the folds: the Stop's one removal can fail on a daemon that is slow
    to answer; the background sweep still runs and removes it."""
    folder = tmp_path / "data"
    folder.mkdir()
    (fake_docker / "claim-slow").write_text("", encoding="utf-8")
    nonce = "c" * 32
    monkeypatch.setattr(docker.uuid, "uuid4", lambda: SimpleNamespace(hex=nonce))
    cancel = threading.Event()
    tries: list[str] = []
    gone = threading.Event()

    def facts(_name: str, timeout: float = 5.0) -> object:
        if not cancel.is_set() or gone.is_set():
            return None
        return docker._ClaimFacts("mine-id", "created", nonce, docker.owner_id())

    def remove(container: str, timeout: float = 5.0) -> bool:
        tries.append(container)
        if len(tries) > 1:
            gone.set()
        return gone.is_set()

    monkeypatch.setattr(docker, "_claim_facts", facts)
    monkeypatch.setattr(docker, "_remove_claim", remove)
    monkeypatch.setattr(docker, "_CLAIM_SWEEP_POLL", 0.05)
    monkeypatch.setattr(docker, "_CLAIM_SWEEP_SECONDS", 10.0)
    worker = threading.Thread(
        target=lambda: pytest.raises(docker.ClaimStopped, _hold, folder, cancel)
    )
    worker.start()
    time.sleep(0.3)
    cancel.set()
    worker.join(HANG_BOUND)
    (fake_docker / "claim-slow").unlink()
    assert gone.wait(HANG_BOUND), f"removal tried {tries}, never done"
    assert tries[:2] == ["mine-id", "mine-id"], tries


def _hold(folder: Path, cancel: threading.Event) -> None:
    with docker.folder_claim(folder, IMAGE, cancel):
        pytest.fail("held a claim after Stop")


def test_a_stopped_daemon_in_todays_words_is_named_as_one() -> None:
    said = docker._claim_refused(
        IMAGE,
        "failed to connect to the docker API at unix:///var/run/docker.sock; check if the "
        "path is correct and if the daemon is running",
    )
    assert said.startswith("Docker is not running"), said


SWEEP_AS_SHIPPED = docker._CLAIM_SWEEP_SECONDS
"""Read at import, before the autouse fixture sets it to 0 for each test."""


def test_the_sweep_outlasts_the_wait_for_a_claim_to_come_up() -> None:
    """Codex review of the folds: a `run` the daemon took late within the 60 s wait must still
    be looked for after it, with margin."""
    assert SWEEP_AS_SHIPPED >= docker._CLAIM_UP_TIMEOUT + 15.0


def test_a_released_claim_whose_removal_failed_is_swept(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review of the folds: letting go of a held claim on a slow daemon -- a failed
    `rm` -- leaves it to the sweep, which removes it by its nonce."""
    folder = tmp_path / "data"
    folder.mkdir()
    tries: list[str] = []
    gone = threading.Event()
    real_remove = docker._remove_claim

    def remove(container: str, timeout: float = 5.0) -> bool:
        tries.append(container)
        if len(tries) == 1:
            return False  # the release's own try: the daemon did not answer in time
        gone.set()
        return real_remove(container, timeout=timeout)

    monkeypatch.setattr(docker, "_remove_claim", remove)
    monkeypatch.setattr(docker, "_CLAIM_SWEEP_POLL", 0.02)
    monkeypatch.setattr(docker, "_CLAIM_SWEEP_SECONDS", 10.0)
    monkeypatch.setattr(docker, "_end_claim_cli", lambda proc, wait=0.0: proc.kill())
    name = _claim_name(folder)

    with docker.folder_claim(folder, IMAGE):
        pass  # the CLI is killed without its stdin closing: the claim stays, as on a slow daemon
    assert gone.wait(HANG_BOUND), f"removal tried {tries}"
    deadline = time.monotonic() + HANG_BOUND
    while name in fake_containers(fake_docker):
        assert time.monotonic() < deadline, "the claim stayed"
        time.sleep(0.02)


# -- T549: a claim that ends before its press does --------------------------------


def test_a_claim_that_ends_while_its_press_holds_it_says_it_was_lost(
    fake_docker: Path, tmp_path: Path
) -> None:
    """T549: Docker Desktop restarted, or the container removed: the claim's CLI exits.

    The press must hear it: a second Yu'lon can claim the folder from then on.
    Ended here as `docker rm -f` ends it, by ending the CLI attached to it.

    Mutation this catches: nothing watching the claim's CLI (`lost` never set).
    """
    folder = tmp_path / "data"
    folder.mkdir()
    name = _claim_name(folder)
    with docker.folder_claim(folder, IMAGE) as held:
        assert not held.lost.is_set()
        cli = int((fake_docker / "containers" / name).read_text(encoding="utf-8"))
        os.kill(cli, signal.SIGKILL)
        assert held.lost.wait(HANG_BOUND), "a claim that ended mid-press was not noticed"
        assert held.name == name


def test_a_claim_its_press_lets_go_is_not_lost(fake_docker: Path, tmp_path: Path) -> None:
    """The press's own release ends the CLI too, and that is not a loss.

    Mutation this catches: the watcher reading every end of the CLI as a loss.
    """
    folder = tmp_path / "data"
    folder.mkdir()
    with docker.folder_claim(folder, IMAGE) as held:
        pass
    time.sleep(0.3)  # the watcher has seen the CLI end by now
    assert not held.lost.is_set()


def test_one_slow_answer_about_a_held_claim_does_not_lose_it(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold review of T549: one `docker inspect` slower than its bound counted as lost, and a
    loss is sticky, so a finished extraction could fail on one slow answer. The claim is
    asked again, with the claim's own bound, before it is called lost.

    Mutations this catches: one ask only; the 1 s look bound instead of `_CLAIM_ASK_TIMEOUT`.
    """
    folder = tmp_path / "data"
    folder.mkdir()
    with docker.folder_claim(folder, IMAGE) as held:
        real = docker._claim_facts
        timeouts: list[float] = []

        def first_slow(name: str, timeout: float = docker._CLAIM_ASK_TIMEOUT) -> object:
            timeouts.append(timeout)
            return None if len(timeouts) == 1 else real(name, timeout=timeout)

        monkeypatch.setattr(docker, "_claim_facts", first_slow)
        assert held.held(), "one slow answer lost the claim"
        assert not held.lost.is_set()
        assert timeouts and all(t == docker._CLAIM_ASK_TIMEOUT for t in timeouts), timeouts


def test_the_two_asks_about_a_held_claim_are_a_moment_apart(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold review 2 of T549: back to back, a transient "no such container" fails both asks in
    milliseconds. The second ask waits `_CLAIM_ASK_GAP`, and only when the first said no.

    Mutations this catches: no wait between the asks; a wait before the first ask too.
    """
    folder = tmp_path / "data"
    folder.mkdir()
    with docker.folder_claim(folder, IMAGE) as held:
        real = docker._claim_facts
        events: list[str] = []
        asked = [0]

        def transient(name: str, timeout: float = docker._CLAIM_ASK_TIMEOUT) -> object:
            events.append("ask")
            asked[0] += 1
            return None if asked[0] == 1 else real(name, timeout=timeout)

        monkeypatch.setattr(docker, "_claim_facts", transient)
        asker = threading.current_thread()
        real_sleep = time.sleep

        def only_ours(seconds: float) -> None:
            # `docker.time` is the one `time` module: a sleep on any other thread (the
            # claim's watcher, the fake daemon) must not count as the asks' wait.
            if threading.current_thread() is asker and seconds == docker._CLAIM_ASK_GAP:
                events.append(f"sleep {seconds}")  # the asks' wait, not a poll's back-off
            else:
                real_sleep(seconds)

        monkeypatch.setattr(docker.time, "sleep", only_ours)
        assert held.held(), "a transient no lost the claim"
        assert events == ["ask", f"sleep {docker._CLAIM_ASK_GAP}", "ask"], events
        events.clear()
        assert held.held()
        assert events == ["ask"], "a claim that answers at once is not made to wait"
    assert 1.0 <= docker._CLAIM_ASK_GAP <= 2.0
