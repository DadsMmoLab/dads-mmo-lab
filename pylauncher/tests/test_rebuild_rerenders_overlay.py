"""A Rebuild writes the build overlay again before it fingerprints and compiles (T740).

T733's measurement (`.notes/gates/t733-build-context-2026-10-12/`): yulon-win11's
playerbots install held a `docker-compose.build.yml` written 2026-09-12, before T525
gave the world and auth servers one `x-yulon-buildx-group`. No Rebuild wrote it again,
so authserver built on a buildx folder of its own and sent its whole 1.41-1.53 GB
context cold. These tests make an install the real install engine leaves, take T525's
key back out of its overlay, and read what the build and the fingerprint (T224's F0)
are handed.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tests.support_native import Recorder, engine
from tests.test_rebuild import (  # noqa: F401 - `cmangos_gate` is a fixture
    _Daemon,
    _daemon_for,
    _refs,
    _seams_of,
    a_finished_cmangos_install,
    a_finished_install,
    cm_engine,
    cmangos_gate,
)
from tests.test_rebuild_parks_a_finished_build import (
    CHANGED,
    REUSED,
    _parked_once,
)
from yulon import docker
from yulon.catalog import build_context, composegen, native
from yulon.catalog.installer import InstallerError, InstallOptions

GROUP_LINE = b"    x-yulon-buildx-group: server-source\n"
OVERLAY_CLAUSE = "it writes docker-compose.build.yml"


def _before_t525(server_dir: Path) -> tuple[bytes, bytes]:
    """Take T525's group key out of the overlay, as an install made before T525 has it.

    Returns:
        The overlay as this version renders it, and as the old install has it.
    """
    path = server_dir / composegen.BUILD_FILE
    fresh = path.read_bytes()
    assert fresh.count(GROUP_LINE) == 2, "the WotLK overlay no longer groups world and auth"
    old = fresh.replace(GROUP_LINE, b"")
    path.write_bytes(old)
    return fresh, old


def _overlay(server_dir: Path) -> bytes:
    return (server_dir / composegen.BUILD_FILE).read_bytes()


def _note(said: list[str]) -> str:
    return next(line for line in said if line.startswith("You can stop this at any time"))


def test_a_rebuild_of_a_pre_t525_install_builds_and_fingerprints_the_current_overlay(
    tmp_path: Path,
) -> None:
    """Read inside the seams, at the moment each is called, for T8's test's reason."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    fresh, _old = _before_t525(server_dir)
    fingerprinted: list[bytes] = []
    built: list[bytes] = []

    def fingerprint(folder: Path, *, refs: object) -> str | None:
        fingerprinted.append(_overlay(folder))
        return build_context.fingerprint(folder, refs=refs)  # type: ignore[arg-type]

    def build(
        folder: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        rec.calls.append("build")
        built.append(_overlay(folder))
        return rec.build_result

    said = list(
        engine(rec, build=build, context_fingerprint=fingerprint).rebuild(
            InstallOptions(server_dir=server_dir)
        )
    )
    assert built == [fresh], "the compile was handed the overlay from before T525"
    assert (
        fingerprinted and fingerprinted[0] == fresh
    ), "F0 was taken over the old overlay, so the kept-build rule compared the wrong files"
    assert _overlay(server_dir) == fresh
    assert OVERLAY_CLAUSE in _note(said), _note(said)
    # WotLK's Rebuild did four things (T706); writing the overlay is a fifth.
    assert "five things" in _note(said), _note(said)
    assert any(line.startswith(f"Wrote {composegen.BUILD_FILE} again") for line in said), said
    stages = [line for line in said if line.startswith("--- ")]
    assert stages == ["--- build", "--- recreate", "--- ready"], said


def test_a_current_overlay_is_left_alone_and_not_mentioned(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    path = server_dir / composegen.BUILD_FILE
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert OVERLAY_CLAUSE not in _note(said), _note(said)
    assert "four things" in _note(said), _note(said)
    assert not any(composegen.BUILD_FILE in line for line in said), said


def test_a_failed_compile_puts_the_old_overlay_back(tmp_path: Path) -> None:
    """The opening note's promise: stopped before the containers move, the folder is as it was."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    _fresh, old = _before_t525(server_dir)
    rec.build_result = docker.AttachedRun(1, ("the compiler said no",))
    said: list[str] = []
    with pytest.raises(InstallerError):
        for line in engine(rec).rebuild(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert _overlay(server_dir) == old, "a failed compile left the new overlay behind"
    assert any("put back exactly as it was" in line for line in said), said


def test_a_stop_during_the_compile_puts_the_old_overlay_back(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    fresh, old = _before_t525(server_dir)
    pressed = threading.Event()
    seen: list[bytes] = []

    def build(
        folder: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        seen.append(_overlay(folder))
        pressed.set()
        return docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("stopped",))

    with pytest.raises(InstallerError):
        list(
            engine(rec, build=build).rebuild(InstallOptions(server_dir=server_dir), cancel=pressed)
        )
    assert seen == [fresh]
    assert _overlay(server_dir) == old


def test_an_overlay_that_cannot_be_rendered_leaves_the_old_one_and_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before T740 no Rebuild wrote the overlay; a render that fails is no worse than that."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    _fresh, old = _before_t525(server_dir)

    def broken(*_args: object, **_kwargs: object) -> str:
        raise composegen.ComposeGenError("the template is gone")

    monkeypatch.setattr(composegen, "render_build", broken, raising=True)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, rec.calls
    assert _overlay(server_dir) == old
    told = [line for line in said if "the template is gone" in line]
    assert len(told) == 1 and "uses the one already in the folder" in told[0], said
    assert OVERLAY_CLAUSE not in _note(said), _note(said)


def test_an_overlay_that_cannot_be_written_leaves_the_old_one_and_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    _fresh, old = _before_t525(server_dir)

    def full(*_args: object, **_kwargs: object) -> bool:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(composegen, "write_build", full, raising=True)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, rec.calls
    assert _overlay(server_dir) == old
    told = [line for line in said if "No space left on device" in line]
    assert len(told) == 1 and "uses the one already in the folder" in told[0], said


def test_a_kept_build_made_on_the_old_overlay_is_removed_and_the_server_compiled(
    tmp_path: Path,
) -> None:
    """The decision for the first Rebuild after T740 on an install that holds a kept build.

    A kept build made by an older Yu'lon recorded a fingerprint over the overlay from
    before T525. The overlay is written again before F0, so F0 differs and T224's
    exact-match rule removes the kept build and compiles. Simulated by giving the
    record the fingerprint of the folder with the old overlay in it.
    """
    rec, daemon, server_dir = _parked_once(tmp_path)
    _before_t525(server_dir)
    record_path = server_dir / native.PARKED_BUILD_FILE
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["fingerprint"] = build_context.fingerprint(server_dir, refs=_refs(server_dir))
    assert record["fingerprint"] is not None
    record_path.write_text(json.dumps(record), encoding="utf-8")
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" in rec.calls, rec.calls
    assert not any(REUSED in line for line in said), said
    assert any(CHANGED in line for line in said), said
    assert native.read_parked_build(server_dir) is None


def test_a_build_kept_after_the_overlay_was_written_again_is_used_next_time(
    tmp_path: Path,
) -> None:
    """The other half: F0 and the kept build's record are both taken over the new overlay.

    The press that keeps its build puts the OLD overlay back (nothing was replaced), and
    the next press writes it again before F0, so the two fingerprints agree.
    """
    rec, daemon, server_dir = _parked_after_a_rerender(tmp_path)
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" not in rec.calls, rec.calls
    assert any(REUSED in line for line in said), said


def _parked_after_a_rerender(tmp_path: Path) -> tuple[Recorder, _Daemon, Path]:
    """`_parked_once()` on an install whose overlay was from before T525 when it was pressed."""
    from tests.test_rebuild_parks_a_finished_build import (
        _parked_on,
        _refused,
        _silent_recreate,
        a_parkable_install,
    )

    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    _fresh, old = _before_t525(server_dir)
    daemon = _daemon_for(server_dir)
    _refused(rec, server_dir, _silent_recreate(rec, daemon))
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    assert native.read_parked_build(server_dir) is not None
    assert _overlay(server_dir) == old, "the keeping press left the new overlay behind"
    rec.calls.clear()
    rec.ready_specs.clear()
    return rec, daemon, server_dir


@pytest.mark.usefixtures("cmangos_gate")
def test_a_cmangos_rebuild_writes_its_overlay_again_too(tmp_path: Path) -> None:
    """The overlay is every family's; CMaNGOS's Update route does not write it (T173)."""
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    path = server_dir / composegen.BUILD_FILE
    fresh = path.read_bytes()
    path.write_bytes(fresh + b"# a line the template has since dropped\n")
    built: list[bytes] = []

    def build(
        folder: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        rec.calls.append("build")
        built.append(_overlay(folder))
        return rec.build_result

    said = list(cm_engine(rec, build=build).rebuild(InstallOptions(server_dir=server_dir)))
    assert built == [fresh]
    stages = [line for line in said if line.startswith("--- ")]
    assert stages == ["--- write-dockerfile", "--- build", "--- recreate", "--- ready"], said
