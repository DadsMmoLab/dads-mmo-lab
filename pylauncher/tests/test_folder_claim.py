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
import subprocess
import sys
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
