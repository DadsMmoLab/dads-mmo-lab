"""Advice that sends the player to a press names a press that recovers, proved by pressing it.

T163 and T164 had one shape. A sentence on a path that has already gone wrong
told the player to press "Rebuild the server…", and on exactly the state that
sentence is said in, Rebuild does not recover it:

* **T163, WotLK.** "Update the server to latest…" failed, the sources went
  back, and Yu'lon's `docker-compose.yml` could not be written into the
  checkout again, so the repository's own file is there. `rebuild()`'s guard
  refuses a compose file Yu'lon did not write.
* **T163, CMaNGOS.** The same press, and the carried source patch could not be
  written again. Rebuild does NOT refuse there: it compiles the unpatched
  source, which is the defect the patch exists to stop.
* **T164.** "Reset to default" found the server's image gone and said "rebuild
  the server first, then reset". `rebuild()` keeps the image it is about to
  compile over as a rollback, and with no image there it refuses.

What recovers each is pressed here too: the install, resumed into the same
folder, for the compose file and the missing image (on a server Yu'lon knows,
the Catalog tile is greyed, so "Remove from Yu'lon…" comes first -- it keeps
the folder, the database and the images); and the same Server build press
again for the patch, because that press writes the patch before it compiles
and again when it puts the sources back.

Every test drives the whole sequence through the real engine: the press that
failed, the sentence it said, the press the old sentence named and what it
really does on that state, and the press the new sentence names. Unit-tested
only; no box was driven.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from tests.support_native import ENTRY, VMAP_FIXTURE, Recorder, engine, install, lay_patch_sources
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_families_cmangos import client_folder
from tests.test_families_cmangos import engine as tbc_engine
from tests.test_families_cmangos import install as tbc_install
from yulon import forgetting, reset_defaults, server_build_presses
from yulon.catalog import composegen, native
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.docker import AttachedRun

OLD = "a" * 40
NEW = "b" * 40


@pytest.fixture(autouse=True)
def _gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """`test_families_cmangos.gated`, which `tbc_engine()` depends on (see that fixture)."""

    def gate(self: CmangosInstaller, ctx: native.StageContext) -> native.ImportGate:
        attached = getattr(self, "_test_gate", None)
        assert attached is not None, "build CMaNGOS engines with tbc_engine(rec)"
        return attached

    monkeypatch.setattr(CmangosInstaller, "_gate", gate, raising=True)


def _said(run: Iterable[str]) -> tuple[list[str], InstallerError | None]:
    """Every line a press yields, and what it raised: `list()` loses the lines on a raise."""
    lines: list[str] = []
    try:
        for line in run:
            lines.append(line)
    except InstallerError as exc:
        return lines, exc
    return lines, None


def _stopped(container: str) -> bool:
    """The world is down: "Remove from Yu'lon…" stops the server before it lets go of it."""
    return False


# -- T163: the update that could not write Yu'lon's own files back ------------


def test_a_compose_file_that_could_not_be_written_back_names_the_install_and_it_recovers(
    tmp_path: Path,
) -> None:
    """Update to latest → compile fails → sources back → compose NOT back → the advice, pressed.

    `restore_rev()` is a `checkout --force`, which puts the repository's tracked
    `docker-compose.yml` back, and here the second half -- writing Yu'lon's file
    over it -- is refused by the disk (a read-only file; a chmod or a full disk
    is what `write_plan()` meets). The restore models git: head back on OLD,
    and the file holds exactly what git has, so `git status` calls it unmodified.

    Then the reason is fixed and:

    1. Rebuild -- what the sentence named until T163 -- REFUSES that folder.
    2. The install, run into the same folder as the new sentence says,
       finishes, puts Yu'lon's compose back, and compiles nothing.
    3. Rebuild now builds.
    """
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    base = server_dir / composegen.BASE_FILE
    upstream = rec.tracked[base]

    def restore_like_git_onto_a_locked_file(dest: Path, rev: str) -> None:
        rec.calls.append(f"restore:{dest.name}->{rev[:7]}")
        rec.heads[dest] = rev
        if dest == server_dir:
            base.chmod(0o644)
            base.write_text(upstream, encoding="utf-8")
            base.chmod(0o444)

    rec.build_result = AttachedRun(2, ("error: no",))
    try:
        said, raised = _said(
            engine(rec, restore_rev=restore_like_git_onto_a_locked_file).update_to_latest(
                InstallOptions(server_dir=server_dir)
            )
        )
    finally:
        base.chmod(0o644)  # the reason, fixed

    assert raised is not None and native.SOURCES_PUT_BACK_NOTE in str(raised)
    assert rec.heads[server_dir] == OLD
    assert base.read_text(encoding="utf-8") == upstream, "the compose was written back after all"
    advice = next(line for line in said if "back on their old commits, but" in line)
    assert server_build_presses.REBUILD not in advice, f"it names the press that refuses: {advice}"
    assert native.INSTALL_AGAIN_HERE in advice, advice

    rec.build_result = AttachedRun(0, ("built",))
    rec.calls.clear()
    with pytest.raises(InstallerError) as refused:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "not written by Yu'lon" in str(refused.value)
    assert "build" not in rec.calls, "the refused rebuild compiled anyway"

    list(engine(rec).run(InstallOptions(server_dir=server_dir)))
    assert composegen.GENERATED_MARKER in base.read_text(encoding="utf-8")
    assert "build" not in rec.calls, "the install compiled a server whose images are all there"

    _, raised = _said(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert raised is None, raised
    assert "build" in rec.calls


def test_a_patch_that_could_not_be_written_back_names_the_same_press_and_it_recovers(
    tmp_path: Path,
) -> None:
    """The CMaNGOS half: the file that could not go back is the carried patch, not compose.

    The restore models `checkout --force` on the core: the files the patch
    edits get the repository's unpatched bytes back, and are then read-only,
    so `apply_carried_patches()` cannot write them.

    Rebuild, pressed on that state, does not refuse -- it compiles, with the
    extractor source unpatched. That is why the advice may not name it. The
    install would not do either: `patch-sources` refuses a tree whose build it
    would skip. What does recover is the press that failed, pressed again: it
    writes the patch before its compile, and writes it again when it puts the
    sources back.
    """
    rec = Recorder()
    server_dir = tmp_path / "tbc"
    tbc_install(rec, server_dir, client_folder(tmp_path))
    for source in TBC.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    rec.on_clone = None
    core = server_dir / "src/mangos-tbc"
    patched = sorted(core.rglob("*.cpp"))
    assert patched, "the fixture laid no file for the patch to edit"
    patched_bytes = {path: path.read_bytes() for path in patched}
    unpatched = {path: (VMAP_FIXTURE / path.name).read_bytes() for path in patched}
    assert patched_bytes != unpatched

    def restore_like_git_onto_locked_files(dest: Path, rev: str) -> None:
        rec.calls.append(f"restore:{dest.name}->{rev[:7]}")
        rec.heads[dest] = rev
        if dest == core:
            for path, text in unpatched.items():
                path.chmod(0o644)
                path.write_bytes(text)
                path.chmod(0o444)

    rec.build_result = AttachedRun(2, ("error: no",))
    try:
        said, raised = _said(
            tbc_engine(rec, restore_rev=restore_like_git_onto_locked_files).update_to_latest(
                InstallOptions(server_dir=server_dir)
            )
        )
    finally:
        for path in patched:
            path.chmod(0o644)  # the reason, fixed

    assert raised is not None and native.SOURCES_PUT_BACK_NOTE in str(raised)
    assert {path: path.read_bytes() for path in patched} == unpatched
    advice = next(line for line in said if "back on their old commits, but" in line)
    assert server_build_presses.REBUILD not in advice, f"it names the press that compiles: {advice}"
    again = server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)
    assert again in advice, advice

    rec.build_result = AttachedRun(0, ("built",))
    rec.calls.clear()
    _, raised = _said(tbc_engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert raised is None and "build" in rec.calls
    assert {
        path: path.read_bytes() for path in patched
    } == unpatched, "Rebuild wrote the patch -- then it would be a recovery after all"

    # The press again: the fetch lays the repository's unpatched bytes, as a
    # real `reset --hard` does, and the press patches them before it compiles.
    rec.on_clone = lay_patch_sources(TBC)
    _, raised = _said(tbc_engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))
    assert raised is None, raised
    assert {path: path.read_bytes() for path in patched} == patched_bytes


# -- T164: Reset to default with the server's image gone ----------------------


def _reset_seams(rec: Recorder, copies: list[str]) -> reset_defaults.Seams:
    """Reset's own seams, asking the SAME daemon double the engine asks about images.

    The copy records and lays nothing: what is asserted is that Reset got as far
    as reading the image, not what the image holds (`test_reset_defaults.py`).
    """

    def copy(image: str, src: str, dest: Path) -> None:
        copies.append(image)

    return reset_defaults.Seams(
        copy_from_image=copy,
        image_present=rec.images_built,
        platform_id=lambda: "linux",
        bind_label=lambda server_dir: "",
    )


def test_an_image_gone_names_the_install_and_the_install_brings_it_back(tmp_path: Path) -> None:
    """Reset → image gone → the advice → Rebuild (refuses) → the install (compiles) → Reset reads.

    One daemon double answers both questions, so the image Reset finds gone is
    an image the Rebuild refuses without: `conf_image_ref()` is asserted to be
    among `image_refs_at()`, the refs the rollback is kept for.
    """
    rec = Recorder()
    server_dir = tmp_path / "tbc"
    client = client_folder(tmp_path)
    tbc_install(rec, server_dir, client)
    rec.calls.clear()
    made = tbc_engine(rec)
    assert made.conf_image_ref(server_dir) in made.image_refs_at(server_dir)
    rec.images = False

    files = reset_defaults.core_files(TBC)
    copies: list[str] = []
    texts, reasons = reset_defaults.default_texts(
        TBC, server_dir, files, seams=_reset_seams(rec, copies)
    )
    assert texts == {} and set(reasons) == set(files) and copies == []
    advice = reasons[files[0]]
    assert "rebuild" not in advice.lower(), f"it names the press that refuses: {advice}"
    assert native.INSTALL_AGAIN_HERE in advice, advice

    with pytest.raises(InstallerError) as refused:
        list(tbc_engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "not all on the daemon" in str(refused.value)
    assert "build" not in rec.calls, "the refused rebuild compiled anyway"
    assert native.INSTALL_AGAIN_HERE in str(refused.value), "Rebuild's own refusal disagrees"

    list(
        tbc_engine(rec, world_running=_stopped).run(
            InstallOptions(server_dir=server_dir, client_dir=client)
        )
    )
    assert "build" in rec.calls, "the install skipped the compile of a missing image"

    rec.images = True  # what the compile it just ran leaves on the daemon
    reset_defaults.default_texts(TBC, server_dir, files, seams=_reset_seams(rec, copies))
    assert copies == [made.conf_image_ref(server_dir)], "Reset still stopped short of the image"


def test_the_route_names_the_removal_by_its_label_and_what_it_keeps() -> None:
    """The removal is the Server tab's own label, and the sentence says what stays."""
    assert f"“{forgetting.BUTTON_LABEL}”" in native.INSTALL_AGAIN_HERE
    assert "same folder" in native.INSTALL_AGAIN_HERE
    assert "keeps the folder, the database and the images" in native.INSTALL_AGAIN_HERE
