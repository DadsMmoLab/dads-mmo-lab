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
folder, for the compose file and the missing image; and the same Server build
press again for the patch, because that press writes the patch before it
compiles and again when it puts the sources back. The way to the install is
most of the advice (`native.install_again_here()`), and each of its shapes is
driven: a server Yu'lon knows (its tile is greyed, so "Remove from Yu'lon…"
comes first), a second server of the same game listed (the tile stays greyed
until that one goes too -- driven through the real Catalog view), and a
server inside a WSL distro (Yu'lon on Windows cannot install there at all; the
Yu'lon inside the distro that built it can).

Every test drives the whole sequence through the real engine: the press that
failed, the sentence it said, the press the old sentence named and what it
really does on that state, and the press the new sentence names. Unit-tested
only; no box was driven.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu

from tests.conftest import pump_until
from tests.support_native import ENTRY, VMAP_FIXTURE, Recorder, engine, install, lay_patch_sources
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_families_cmangos import client_folder
from tests.test_families_cmangos import engine as tbc_engine
from tests.test_families_cmangos import install as tbc_install
from yulon import forgetting, install_wiring, platform, reset_defaults, server_build_presses
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.docker import AttachedRun

OLD = "a" * 40
NEW = "b" * 40
DISTRO = "Ubuntu"
INSIDE = "/home/pk/wow-wotlk"
"""Where the WotLK fixture lives as the distro sees it; `_in_wsl()` maps the tmp folder here."""


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


class _DiskThatFills:
    """`composegen`'s `os`, with a disk that fills while armed: half a write, then ENOSPC.

    Scoped to `composegen` by replacing its module attribute, so nothing else
    in the process meets the full disk.
    """

    def __init__(self) -> None:
        self.armed = False
        self.writes = 0

    def write(self, fd: int, data: bytes | memoryview) -> int:
        if not self.armed:
            return os.write(fd, data)
        self.writes += 1
        if self.writes == 1:
            return os.write(fd, bytes(data)[: max(1, len(data) // 2)])
        raise OSError(errno.ENOSPC, "No space left on device")

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)


def _wotlk_ready(tmp_path: Path) -> tuple[Recorder, Path]:
    """A finished WotLK install, every source on OLD with NEW upstream."""
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    return rec, server_dir


def _update_that_cannot_write_compose_back(
    rec: Recorder, server_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], InstallerError | None]:
    """Update to latest → the compile fails → sources back → the disk fills on the compose write.

    The restore models `checkout --force`: head back on OLD and the
    repository's tracked `docker-compose.yml` back in the folder, byte for byte
    what git has. Then the disk is full -- the likeliest reason an update
    failed at all is a compile that filled it -- and Yu'lon's file cannot be
    written over it. Disarmed before this returns: that is "the reason, fixed".
    """
    base = server_dir / composegen.BASE_FILE
    upstream = rec.tracked[base]
    disk = _DiskThatFills()
    monkeypatch.setattr(composegen, "os", disk)

    def restore_like_git_then_fill_the_disk(dest: Path, rev: str) -> None:
        rec.calls.append(f"restore:{dest.name}->{rev[:7]}")
        rec.heads[dest] = rev
        if dest == server_dir:
            base.write_text(upstream, encoding="utf-8")
            disk.armed = True

    rec.build_result = AttachedRun(2, ("error: no",))
    try:
        said = _said(
            engine(rec, restore_rev=restore_like_git_then_fill_the_disk).update_to_latest(
                InstallOptions(server_dir=server_dir)
            )
        )
    finally:
        disk.armed = False
    rec.build_result = AttachedRun(0, ("built",))
    assert disk.writes >= 2, "the disk never filled"
    assert rec.heads[server_dir] == OLD
    assert base.read_text(encoding="utf-8") == upstream, "not git's file: the resume would refuse"
    return said


def _in_wsl(monkeypatch: pytest.MonkeyPatch, server_dir: Path) -> None:
    """`server_dir` is, to this process, the WotLK folder inside `DISTRO` (T125's own stand-in).

    The Windows app holds such a folder as `\\\\wsl.localhost\\Ubuntu\\...`;
    `platform.wsl_location()` is what reads that spelling, so answering it for
    the real folder here is the engine's whole view of "this lives in a distro".
    """
    real = platform.wsl_location

    def location(path: Path) -> tuple[str, str] | None:
        return (DISTRO, INSIDE) if Path(path) == server_dir else real(path)

    monkeypatch.setattr(platform, "wsl_location", location)


def _windows_install_is_refused(rec: Recorder) -> None:
    """Both installs Yu'lon on Windows has refuse a folder inside the distro, before writing.

    The Catalog's (default seams: `platform.server_dir_problem()` names the WSL
    share) and the WSL engine's (`Seams.in_wsl()` refuses every install-only
    seam). The folder is the UNC spelling the Windows app would hold.
    """
    unc = platform.wsl_unc_path(DISTRO, INSIDE)
    assert unc is not None
    with pytest.raises(InstallerError, match="is inside WSL"):
        list(engine(rec).run(InstallOptions(server_dir=unc)))
    with pytest.raises(InstallerError, match="does not install into it"):
        list(
            install_wiring.installer_for_app(ENTRY, wsl_distro=DISTRO).run(
                InstallOptions(server_dir=unc)
            )
        )
    assert not unc.exists(), "a refused install wrote into the folder"


# -- T163: the update that could not write Yu'lon's own files back ------------


def test_a_compose_file_the_full_disk_kept_out_names_the_install_and_it_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The advice, then every press it concerns, on the state it is said in.

    1. Rebuild -- what the sentence named until T163 -- REFUSES that folder.
    2. The install, run into the same folder as the new sentence says,
       finishes, puts Yu'lon's compose back, and compiles nothing. It can only
       because the full disk left git's own file whole (`write_plan()` replaces
       whole since T163); a cut-off file would be refused here too.
    3. Rebuild now builds.
    """
    rec, server_dir = _wotlk_ready(tmp_path)
    said, raised = _update_that_cannot_write_compose_back(rec, server_dir, monkeypatch)

    assert raised is not None and native.SOURCES_PUT_BACK_NOTE in str(raised)
    advice = next(line for line in said if "back on their old commits, but" in line)
    assert server_build_presses.REBUILD not in advice, f"it names the press that refuses: {advice}"
    assert native.install_again_here(ENTRY.name, server_dir) in advice, advice
    assert "WSL" not in advice

    rec.calls.clear()
    with pytest.raises(InstallerError) as refused:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "not written by Yu'lon" in str(refused.value)
    assert "build" not in rec.calls, "the refused rebuild compiled anyway"

    list(engine(rec).run(InstallOptions(server_dir=server_dir)))
    base = server_dir / composegen.BASE_FILE
    assert composegen.GENERATED_MARKER in base.read_text(encoding="utf-8")
    assert "build" not in rec.calls, "the install compiled a server whose images are all there"

    _, raised = _said(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert raised is None, raised
    assert "build" in rec.calls


def test_on_a_wsl_server_the_advice_sends_the_player_into_the_distro_and_that_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex, round 2: the same failure on a server Yu'lon on Windows manages inside a distro.

    Nothing on Windows mends it: Rebuild refuses, and both installs refuse the
    folder. The advice says so and names the distro and the folder's Linux
    path; the install run there -- by the Yu'lon for Linux that built the
    server, on the folder as the distro sees it -- mends it.
    """
    rec, server_dir = _wotlk_ready(tmp_path)
    _in_wsl(monkeypatch, server_dir)
    said, _ = _update_that_cannot_write_compose_back(rec, server_dir, monkeypatch)

    advice = next(line for line in said if "back on their old commits, but" in line)
    assert native.install_again_here(ENTRY.name, server_dir) in advice, advice
    assert f"WSL distro {DISTRO}" in advice and f"choose {INSIDE}" in advice, advice
    assert "Yu'lon on Windows cannot" in advice
    assert "in the Catalog and choose" not in advice, "it names the Windows-side route"

    with pytest.raises(InstallerError, match="not written by Yu'lon"):
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    _windows_install_is_refused(rec)

    monkeypatch.undo()  # inside the distro, the folder is a Linux folder
    rec.calls.clear()
    list(engine(rec).run(InstallOptions(server_dir=server_dir)))
    base = server_dir / composegen.BASE_FILE
    assert composegen.GENERATED_MARKER in base.read_text(encoding="utf-8")
    assert "build" not in rec.calls


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
    route = native.install_again_here(TBC.name, server_dir)

    files = reset_defaults.core_files(TBC)
    copies: list[str] = []
    texts, reasons = reset_defaults.default_texts(
        TBC, server_dir, files, seams=_reset_seams(rec, copies)
    )
    assert texts == {} and set(reasons) == set(files) and copies == []
    advice = reasons[files[0]]
    assert "rebuild" not in advice.lower(), f"it names the press that refuses: {advice}"
    assert route in advice, advice

    with pytest.raises(InstallerError) as refused:
        list(tbc_engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "not all on the daemon" in str(refused.value)
    assert "build" not in rec.calls, "the refused rebuild compiled anyway"
    assert route in str(refused.value), "Rebuild's own refusal disagrees"

    list(
        tbc_engine(rec, world_running=_stopped).run(
            InstallOptions(server_dir=server_dir, client_dir=client)
        )
    )
    assert "build" in rec.calls, "the install skipped the compile of a missing image"

    rec.images = True  # what the compile it just ran leaves on the daemon
    reset_defaults.default_texts(TBC, server_dir, files, seams=_reset_seams(rec, copies))
    assert copies == [made.conf_image_ref(server_dir)], "Reset still stopped short of the image"


def test_on_a_wsl_server_an_image_gone_sends_the_player_into_the_distro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuild's own refusal, on a server inside a distro: the route is the distro's.

    Reset never says IMAGE_GONE there (`IN_WSL` answers first, asserted
    below), so the refusal is the one sentence on this state that must carry
    the WSL route, and the install it names is the one that compiles.
    """
    rec, server_dir = _wotlk_ready(tmp_path)
    _in_wsl(monkeypatch, server_dir)
    rec.images = False
    rec.calls.clear()

    with pytest.raises(InstallerError) as refused:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    said = str(refused.value)
    assert native.install_again_here(ENTRY.name, server_dir) in said
    assert f"WSL distro {DISTRO}" in said and "in the Catalog and choose" not in said, said
    assert "build" not in rec.calls
    _windows_install_is_refused(rec)
    _, reasons = reset_defaults.default_texts(
        TBC, tmp_path, ["etc/mangosd.conf"], wsl_distro=DISTRO, seams=_reset_seams(rec, [])
    )
    assert "inside the WSL distro" in reasons["etc/mangosd.conf"]

    monkeypatch.undo()
    list(engine(rec).run(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, "the install in the distro skipped the missing image"


# -- the way to the install: the Catalog as it really is ------------------------


def test_with_a_second_server_of_the_game_listed_the_install_is_reached_as_the_advice_says(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold review SF2, through the real Catalog view: two WotLK servers, one to mend.

    Removing only the one to mend leaves the tile "Installed" and greyed, and
    its menu without "Install Server…" -- the press the advice names cannot be
    reached. Removing the other as well (the advice's second sentence) gives
    the tile back; Install then takes the folder, and "Use existing…" brings
    the other back.
    """
    from tests.test_catalog_view import _FakeInstaller
    from yulon.ui import catalog_view
    from yulon.ui.catalog_view import CatalogView
    from yulon.ui.widgets.log_panel import LogPanel

    catalog = load_catalog()
    wotlk = catalog.get("wow-wotlk")
    mend, other = tmp_path / "mend", tmp_path / "other"
    for folder in (mend, other):
        folder.mkdir()
        (folder / composegen.BASE_FILE).write_text("services: {}\n", encoding="utf-8")
    advice = native.install_again_here(wotlk.name, mend)
    assert f"another {wotlk.name} server" in advice and native.USE_EXISTING_LABEL in advice

    picks = [mend, other]
    made: list[_FakeInstaller] = []

    def make(entry: Any) -> _FakeInstaller:
        made.append(_FakeInstaller(entry, []))
        return made[-1]

    view = CatalogView(
        catalog,
        make,
        LogPanel(),
        pick_dir=lambda *_: picks.pop(0),
        ask_suggestion=lambda *_: False,
        home=tmp_path,
        installed_games={"wow-wotlk": other},
    )
    menus: list[list[str]] = []

    class RecordingMenu(QMenu):
        """The tile's own menu, built by the view; `exec` records it instead of blocking."""

        def exec(self, *_args: object) -> None:  # type: ignore[override]
            menus.append([action.text() for action in self.actions()])

    monkeypatch.setattr(catalog_view, "QMenu", RecordingMenu)

    def menu() -> list[str]:
        view._show_tile_context_menu(QPoint(0, 0), wotlk, view)
        return menus[-1]

    view.forget_installed("wow-wotlk", {"wow-wotlk": other})  # the one to mend goes
    assert view.button_for("wow-wotlk").isEnabled() is False
    assert "Install Server…" not in menu(), "the tile offers Install with another listed"

    view.forget_installed("wow-wotlk", {})  # ...and the other one too
    assert view.button_for("wow-wotlk").isEnabled() is True
    assert "Install Server…" in menu()
    assert view.start_install(wotlk) is True
    pump_until(lambda: bool(made and made[-1].ran_with), "the install into the folder")
    assert made[-1].ran_with[0].server_dir == mend

    assert view.existing_button_for("wow-wotlk").text() == native.USE_EXISTING_LABEL
    pump_until(lambda: view.existing_button_for("wow-wotlk").isEnabled(), "the tile to unlock")
    assert view.attach_existing(wotlk) is True, "the other server did not come back"


def test_the_route_names_the_removal_and_the_way_back_by_their_labels(tmp_path: Path) -> None:
    """The presses are named as the app labels them, and the sentence says what stays."""
    advice = native.install_again_here("WoW WotLK", tmp_path / "wow")
    assert f"\u201c{forgetting.BUTTON_LABEL}\u201d" in advice
    assert "keeps the folder, the database and the images" in advice
    assert f"choose {tmp_path / 'wow'}" in advice
    assert f"\u201c{native.USE_EXISTING_LABEL}\u201d" in advice
