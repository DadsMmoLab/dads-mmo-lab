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

from tests.support_native import ENTRY, Recorder, engine
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
    a_parkable_install,
)
from yulon import docker, resources
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


def test_an_overlay_that_cannot_be_made_leaves_the_old_one_and_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Before T740 no Rebuild wrote the overlay; a template that fails is no worse than that.

    Round 2: the reason goes to the log, not into the panel's sentence.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    _fresh, old = _before_t525(server_dir)

    def broken(*_args: object, **_kwargs: object) -> str:
        raise composegen.ComposeGenError("the template is gone")

    monkeypatch.setattr(composegen, "render_build", broken, raising=True)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, rec.calls
    assert _overlay(server_dir) == old
    told = [line for line in said if composegen.BUILD_FILE in line]
    assert len(told) == 1 and "could not prepare" in told[0], said
    assert "uses the one already in the folder" in told[0] and "render" not in told[0], told
    assert not any("the template is gone" in line for line in said), said
    assert "the template is gone" in caplog.text
    assert OVERLAY_CLAUSE not in _note(said), _note(said)


def test_an_overlay_that_cannot_be_read_leaves_it_and_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    _fresh, old = _before_t525(server_dir)

    def unreadable(*_args: object, **_kwargs: object) -> bool:
        raise PermissionError(13, "Permission denied", "/srv/x/docker-compose.build.yml")

    monkeypatch.setattr(composegen, "build_is_current", unreadable, raising=True)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, rec.calls
    assert _overlay(server_dir) == old
    told = [line for line in said if composegen.BUILD_FILE in line]
    assert len(told) == 1 and "could not read" in told[0], said
    assert "render" not in told[0] and "prepare" not in told[0], told
    assert not any("Permission denied" in line for line in said), said
    assert "Permission denied" in caplog.text


def test_an_overlay_that_cannot_be_written_leaves_the_old_one_and_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
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
    told = [line for line in said if "could not be written again" in line]
    assert len(told) == 1 and "uses the one already in the folder" in told[0], said
    assert not any("No space left on device" in line for line in said), said
    assert "No space left on device" in caplog.text
    assert not any(line.startswith(f"Wrote {composegen.BUILD_FILE}") for line in said), said


def test_a_crlf_copy_of_the_current_overlay_is_neither_written_nor_said_to_be(
    tmp_path: Path,
) -> None:
    """Round 2: the plan and the write ask one question (`composegen.build_is_current()`).

    The write reads text, so a CRLF copy of the current overlay is the same text to it.
    A plan that compared bytes said "Wrote ... again" and told the note, and wrote nothing.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    path = server_dir / composegen.BUILD_FILE
    crlf = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(crlf)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert path.read_bytes() == crlf
    assert not any(composegen.BUILD_FILE in line for line in said if line != _note(said)), said
    assert OVERLAY_CLAUSE not in _note(said), _note(said)


def _kept_over_the_old_overlay(tmp_path: Path) -> tuple[Recorder, _Daemon, Path, bytes, bytes]:
    """A kept build an older Yu'lon made over the overlay from before T525.

    Simulated by giving `_parked_once()`'s record the fingerprint of the folder with the
    old overlay in it.
    """
    rec, daemon, server_dir = _parked_once(tmp_path)
    fresh, old = _before_t525(server_dir)
    record_path = server_dir / native.PARKED_BUILD_FILE
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["fingerprint"] = build_context.fingerprint(server_dir, refs=_refs(server_dir))
    assert record["fingerprint"] is not None
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return rec, daemon, server_dir, fresh, old


def test_a_kept_build_made_on_the_old_overlay_is_used_and_the_overlay_waits(
    tmp_path: Path,
) -> None:
    """Round 2, the coordinator's decision: the kept build is used, as the confirmation says.

    The overlay is left as it is on that press and written by the next, which has no
    record once the kept build was used.
    """
    rec, daemon, server_dir, fresh, old = _kept_over_the_old_overlay(tmp_path)
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" not in rec.calls, rec.calls
    assert any(REUSED in line for line in said), said
    assert _overlay(server_dir) == old
    assert any("left as it is for now" in line for line in said), said
    assert not any(line.startswith(f"Wrote {composegen.BUILD_FILE}") for line in said), said
    assert "unless the kept build can be used with the file as it is" in _note(said)
    assert native.read_parked_build(server_dir) is None
    rec.calls.clear()
    again = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" in rec.calls, rec.calls
    assert _overlay(server_dir) == fresh
    assert OVERLAY_CLAUSE in _note(again) and "unless the kept build" not in _note(again)


def test_a_kept_build_whose_files_changed_compiles_with_the_new_overlay(tmp_path: Path) -> None:
    from tests.test_rebuild_parks_a_finished_build import SOURCE

    rec, daemon, server_dir, fresh, _old = _kept_over_the_old_overlay(tmp_path)
    (server_dir / SOURCE).write_text("void World::Update() { tick(); }\n", encoding="utf-8")
    built: list[bytes] = []
    seams = _seams_of(rec, daemon)
    inner = seams["build"]
    assert callable(inner)

    def build(folder: Path, *args: object, **kw: object) -> object:
        built.append(_overlay(folder))
        return inner(folder, *args, **kw)

    said = list(
        engine(rec, **{**seams, "build": build}).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert built == [fresh], "the compile was handed the old overlay"
    assert any(CHANGED in line for line in said), said
    assert native.read_parked_build(server_dir) is None


def test_a_build_kept_after_the_overlay_was_written_again_is_used_next_time(
    tmp_path: Path,
) -> None:
    """The keeping press puts the OLD overlay back after F1 was taken over the new one.

    Round 2: with a record, the first F0 (old overlay) does not match, so the overlay is
    written and F0 taken again, which matches, and the kept build is used.
    """
    rec, daemon, server_dir = _parked_after_a_rerender(tmp_path)
    fresh = composegen.render_build(ENTRY, templates_root=resources.installers_dir())
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" not in rec.calls, rec.calls
    assert any(REUSED in line for line in said), said
    assert _overlay(server_dir) == fresh.encode("utf-8")


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


def test_the_confirmation_names_the_overlay_for_every_native_game_without_reading_it(
    tmp_path: Path,
) -> None:
    """Round 2 (cold review): an edit under the marker is replaced, and the question says so.

    The folder does not exist, so a confirmation that read it to decide would fail or drop
    the sentence; it is static. `kept_build=False` keeps the kept-build read out too.
    """
    from yulon.catalog.catalog import load_catalog
    from yulon.catalog.installer import rebuild_confirmation

    entries = [e for e in load_catalog().games if e.install.native is not None]
    assert len(entries) >= 4, "the catalog lost its native entries"
    for entry in entries:
        text = rebuild_confirmation(entry, tmp_path / "absent", kept_build=False)
        paragraph = next((p for p in text.split("\n\n") if composegen.BUILD_FILE in p), "")
        assert paragraph, (entry.id, text)
        assert "including anything you changed underneath it" in paragraph, paragraph
        assert "put back as it was" in paragraph, paragraph
    assert not (tmp_path / "absent").exists()


def _stopped_and_landed(tmp_path: Path) -> tuple[Recorder, _Daemon, Path, bytes, bytes]:
    """A press stopped mid-compile on a pre-T525 overlay, whose build then landed (T225).

    The press is this version's, so it wrote the new overlay before F0, recorded that F0
    for the stopped build and put the old overlay back. `rebuild()` settles the landed
    build into a kept build (`_settle_stopped_build()` -> `_park_landed()`) AFTER the
    point where round 2 read whether a kept build was recorded.
    """
    from tests.test_stop_mid_compile import _stopped_mid_compile

    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    fresh, old = _before_t525(server_dir)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    assert native.read_stopped_build(server_dir) is not None
    assert native.read_parked_build(server_dir) is None
    assert _overlay(server_dir) == old
    daemon.compiled()
    return rec, daemon, server_dir, fresh, old


def test_a_build_stopped_before_t740_that_landed_is_used_with_the_old_overlay(
    tmp_path: Path,
) -> None:
    """Round 3 (re-review): the stopped press was an older Yu'lon's, F0 over the old file.

    Simulated by giving the stopped build's record the fingerprint of the folder with the
    old overlay in it. Its build becomes a kept build only inside `rebuild()`, so the
    question "is a build kept?" has to be asked in the build stage, not before.
    """
    from tests.test_stop_mid_compile import _seams_of as _stop_seams_of

    rec, daemon, server_dir, _fresh, old = _stopped_and_landed(tmp_path)
    record_path = server_dir / native.STOPPED_BUILD_FILE
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["fingerprint"] = build_context.fingerprint(server_dir, refs=_refs(server_dir))
    assert record["fingerprint"] is not None
    record_path.write_text(json.dumps(record), encoding="utf-8")
    said = list(
        engine(rec, **_stop_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" not in rec.calls, rec.calls
    assert any(REUSED in line for line in said), said
    assert _overlay(server_dir) == old


def test_a_build_stopped_after_t740_that_landed_is_used_and_the_overlay_written(
    tmp_path: Path,
) -> None:
    from tests.test_stop_mid_compile import _seams_of as _stop_seams_of

    rec, daemon, server_dir, fresh, _old = _stopped_and_landed(tmp_path)
    said = list(
        engine(rec, **_stop_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" not in rec.calls, rec.calls
    assert any(REUSED in line for line in said), said
    assert _overlay(server_dir) == fresh


def test_the_overlay_left_line_is_said_only_once_the_kept_build_was_used(tmp_path: Path) -> None:
    """Round 3 wording: a record that matches but whose images are gone is not used.

    F0 matched with the old overlay, so the overlay was left as it is, but the kept
    build's `-parked` names are gone, so the server is compiled; the line that says the
    file was left for the kept build is not said.
    """
    rec, daemon, server_dir, _fresh, old = _kept_over_the_old_overlay(tmp_path)
    for ref in _refs(server_dir):
        daemon.names.pop(ref + native.PARKED_TAG_SUFFIX, None)
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" in rec.calls, rec.calls
    assert native.OVERLAY_LEFT_FOR_THE_KEPT_BUILD not in said, said
    assert _overlay(server_dir) == old
