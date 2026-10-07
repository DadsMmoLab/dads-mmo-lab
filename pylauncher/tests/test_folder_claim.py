"""T543 + T544: two Yu'lons on one Docker daemon, one server folder.

T544: a running extraction tool another Yu'lon made is told apart from one this Yu'lon
left: the press says another Yu'lon is extracting there and never offers to remove its
container. T543: a press claims the folder with a container named by the folder's id
(T536), so the daemon decides which of two presses at once goes ahead.

The docker CLI is `support_fake_docker`'s.
"""

from __future__ import annotations

import logging
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
