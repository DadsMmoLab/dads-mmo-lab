"""The uninstall's Docker primitives against a real daemon (T705, relationship item 4).

`purge.py` rests on "every command is scoped to the compose project": the volumes it
deletes are the ones `docker.project_volumes()` finds by compose's project label, and
each image goes by its exact reference. Every unit test proves that on a fake that
asserts the argv. Here the daemon answers: two compose projects with the same volume
key, a volume that wears our project's name but was never compose's, and an image
tag beside ours on the same layers.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.integration.conftest import _RUN_TAG, BUSYBOX_IMAGE
from yulon import docker

pytestmark = pytest.mark.integration

OURS = f"yulon-it-{_RUN_TAG}-ours"
THEIRS = f"yulon-it-{_RUN_TAG}-theirs"
LOOKALIKE = f"{OURS}_spare"
"""Our project's name and a volume suffix, made by hand: no compose label on it."""

_COMPOSE_YML = f"""\
services:
  holder:
    image: {BUSYBOX_IMAGE}
    command: ["true"]
    volumes:
      - data:/data
volumes:
  data:
"""


def _docker(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *argv], cwd=cwd, capture_output=True, text=True, check=False, timeout=120
    )


def _volume_there(name: str) -> bool:
    return _docker("volume", "inspect", name).returncode == 0


def _image_there(ref: str) -> bool:
    return _docker("image", "inspect", ref).returncode == 0


@pytest.fixture
def two_projects(tmp_path: Path, require_docker: None) -> Iterator[Path]:
    """Ours and theirs, each with its `data` volume and a created (never started) container."""
    folder = tmp_path / "compose"
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(_COMPOSE_YML, encoding="utf-8")
    projects = (OURS, THEIRS)

    def down() -> None:
        for project in projects:
            _docker("compose", "-p", project, "down", "-v", "--timeout", "2", cwd=folder)
        _docker("volume", "rm", "-f", LOOKALIKE)

    down()
    try:
        assert _docker("pull", "-q", BUSYBOX_IMAGE).returncode == 0 or _image_there(BUSYBOX_IMAGE)
        for project in projects:
            made = _docker("compose", "-p", project, "up", "--no-start", cwd=folder)
            assert made.returncode == 0, made.stderr
        assert _docker("volume", "create", LOOKALIKE).returncode == 0
        yield folder
    finally:
        down()


def test_project_volumes_finds_exactly_the_volumes_compose_made_for_this_project(
    two_projects: Path,
) -> None:
    assert docker.project_volumes(OURS) == [f"{OURS}_data"]
    assert docker.project_volumes(THEIRS) == [f"{THEIRS}_data"]


def test_a_volume_still_held_is_refused_and_a_freed_one_goes_alone(two_projects: Path) -> None:
    ours = f"{OURS}_data"
    with pytest.raises(docker.DockerCommandError):
        docker.remove_volume(ours)  # the created container still references it
    assert _volume_there(ours)
    removed = _docker("compose", "-p", OURS, "rm", "-f", cwd=two_projects)
    assert removed.returncode == 0, removed.stderr

    docker.remove_volume(ours)

    assert not _volume_there(ours)
    assert _volume_there(f"{THEIRS}_data"), "the other project's volume of the same key went"
    assert _volume_there(LOOKALIKE), "a volume that only wears our name went"


@pytest.fixture
def two_tags(require_docker: None) -> Iterator[tuple[str, str]]:
    """Two names for one image, as two installs' builds of the same layers would have."""
    ours = f"yulon.local/it-{_RUN_TAG}:native-ours"
    theirs = f"yulon.local/it-{_RUN_TAG}:native-theirs"
    try:
        assert _docker("pull", "-q", BUSYBOX_IMAGE).returncode == 0 or _image_there(BUSYBOX_IMAGE)
        for ref in (ours, theirs):
            assert _docker("image", "tag", BUSYBOX_IMAGE, ref).returncode == 0
        yield ours, theirs
    finally:
        for ref in (ours, theirs):
            _docker("image", "rm", ref)


def test_remove_image_takes_exactly_the_one_reference(two_tags: tuple[str, str]) -> None:
    ours, theirs = two_tags
    assert docker.remove_image(ours) == ""
    assert not _image_there(ours)
    assert _image_there(theirs), "a second name for the same layers went"
    assert _image_there(BUSYBOX_IMAGE), "the shared base image went"
    assert docker.remove_image(ours) == "", "an image already gone is not a failure"
