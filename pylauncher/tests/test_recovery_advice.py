"""One press recovers each state a failed press leaves, proved by pressing it (T163, T164, T170).

T163 and T164 had one shape. A sentence on a path that has already gone wrong
told the player to press "Rebuild the server…", and on exactly the state that
sentence is said in, Rebuild did not recover it:

* **WotLK.** "Update the server to latest…" failed, the sources went back, and
  Yu'lon's `docker-compose.yml` could not be written into the checkout again,
  so the repository's own file is there. `rebuild()`'s guard refuses a compose
  file Yu'lon did not write.
* **CMaNGOS.** The same press, and the carried source patch could not be
  written again. Rebuild does NOT refuse there: it compiles the unpatched
  source, which is the defect the patch exists to stop.
* **An image gone.** "Reset to default" found the server's image gone and said
  "rebuild the server first, then reset", and `rebuild()` refused without a
  build to keep as a rollback.

T163/T164 sent the first and the third to "Remove from Yu'lon…" and an install
into the same folder, two presses and a trip through the Catalog. T170 (the
owner's "Repair + Rebuild both", 2026-09-28) makes each one press:

* **Repair server files…** replaces the repository's untouched file with
  Yu'lon's, by the install's own `replaceable` rule, keeps a backup and offers
  Recreate -- and still refuses a file somebody changed, or one in a folder
  Yu'lon's record does not say it built there;
* **Rebuild the server…** compiles when the images are gone, after a
  confirmation that says there is no build to keep as a rollback, and says what
  a failure then leaves.

The patch keeps its own press (the same Server build entry again).

Every test drives the sequence through the real engine and the real wiring,
with only docker and git doubled (`tests.support_native.Recorder`): the press
that failed, the sentence it said, and the press that sentence names, on the
state it is said in. Unit-tested only; no box was driven.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox

from tests.support_native import ENTRY, VMAP_FIXTURE, Recorder, engine, install, lay_patch_sources
from tests.test_controller_view import _Ps, _services
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_families_cmangos import client_folder
from tests.test_families_cmangos import engine as tbc_engine
from tests.test_families_cmangos import install as tbc_install
from yulon import install_wiring, platform, reset_defaults, runner, server_build_presses
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions, rebuild_confirmation
from yulon.docker import AttachedRun
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

OLD = "a" * 40
NEW = "b" * 40
DISTRO = "Ubuntu"
INSIDE = "/home/pk/wow-wotlk"
"""Where the WotLK fixture lives as the distro sees it; `_in_wsl()` maps the tmp folder here."""

REBUILD = server_build_presses.under_server_build(server_build_presses.REBUILD)


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


def _built_from_old(server_dir: Path, entry: CatalogEntry) -> None:
    """The install record says the running build was made from OLD, where the heads sit (T217).

    A real install leaves every source on its pin and records nothing; these
    fixtures put the heads on OLD instead, which a plain Rebuild now refuses as a
    folder off the commit its build came from. Recording OLD says what the fixture
    means: OLD IS what the server was built from.
    """
    state = native.read_state(server_dir, valid=())
    assert state is not None
    revs = tuple(
        native.SourceRev(repo=source.repo, built=f"{OLD[:7]} · 2026-09-16")
        for source in entry.emulator.sources
    )
    native.write_state(server_dir, replace(state, source_revs=revs))


def _wotlk_ready(tmp_path: Path) -> tuple[Recorder, Path]:
    """A finished WotLK install, every source on OLD (its build's commit) with NEW upstream."""
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    _built_from_old(server_dir, ENTRY)
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
    assert base.read_text(encoding="utf-8") == upstream, "not git's file: the Repair would refuse"
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


def _wired_to(monkeypatch: pytest.MonkeyPatch, make: Any) -> None:
    """The app's wiring builds its engine through `make`: the real engine over the doubles.

    `installer_for_app()` is the one place the wiring makes an engine, and its
    default seams are the real docker and git. Everything between the tab and
    the engine -- the route, the press, its arguments -- is the shipped code.
    """
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda entry, **kw: make())


def _backups(server_dir: Path) -> list[Path]:
    return sorted(server_dir.glob(composegen.BASE_FILE + ".*" + native.REPAIR_BACKUP_SUFFIX))


def _answer_yes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every question the tab asks is answered Yes; returns what each one said."""
    asked: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> int:
        asked.append(text)
        return int(QMessageBox.StandardButton.Yes.value)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


# -- T170 (a): the repository's own compose file, one Repair press ------------


def test_a_failed_update_is_mended_by_repair_then_recreate_and_rebuild_works(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole sequence, on the WotLK Server tab the app builds, wired as the app wires it.

    1. The update fails and says to press Repair server files…, not Rebuild
       and not the Remove + Install route T163 named.
    2. Rebuild still refuses the folder -- which is why the sentence may not
       name it -- and compiles nothing.
    3. The tab asks again when the press ends, and its banner offers the
       Repair for the repository's file; the press asks, writes Yu'lon's file,
       keeps the repository's as a backup byte for byte, and offers Recreate.
    4. Recreate removes and starts the containers.
    5. Rebuild, through the app's own Rebuild route, now compiles.
    """
    rec, server_dir = _wotlk_ready(tmp_path)
    _wired_to(monkeypatch, lambda: engine(rec))
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    monkeypatch.setattr(runner, "run", _Ps())
    services = _services(_Ps(), server_dir, [])
    services.repair_compose = install_wiring.repair_compose_for_app(ENTRY, server_dir)
    assert services.repair_compose is not None, "WotLK is not wired the Repair"
    view = ControllerView(ENTRY, services, status_poll_ms=0)
    assert view.compose_banner.isHidden(), "a healthy WotLK install was offered a Repair"

    base = server_dir / composegen.BASE_FILE
    upstream = rec.tracked[base]
    view._rebuild_moves_sources = True  # what the Update press sets before it runs
    said, raised = _update_that_cannot_write_compose_back(rec, server_dir, monkeypatch)
    view._rebuild_finished(False, str(raised))

    assert raised is not None and native.SOURCES_PUT_BACK_NOTE in str(raised)
    advice = next(line for line in said if "back on their old commits, but" in line)
    assert f"“{native.REPAIR_FILES_LABEL}” on the Server tab" in advice, advice
    assert f"“{native.RECREATE_CONTAINERS_LABEL}”" in advice, advice
    assert "Remove from Yu'lon" not in advice and "WSL" not in advice, advice

    rec.calls.clear()
    with pytest.raises(InstallerError) as refused:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" not in rec.calls, "the refused rebuild compiled anyway"
    # Cold review SF2: its refusal names the Repair, not "another launcher".
    assert f"\u201c{native.REPAIR_FILES_LABEL}\u201d on the Server tab" in str(refused.value)
    assert "another launcher" not in str(refused.value)

    assert not view.compose_banner.isHidden(), "the tab did not ask again after the update"
    assert view.compose_banner_label.text() == controller_view_module.REPAIR_FILES_UPSTREAM_BANNER
    asked = _answer_yes(monkeypatch)
    view.compose_banner_button.click()
    assert len(asked) == 1 and "came with the server's source code" in asked[0], asked
    assert composegen.is_marker_line(base.read_text(encoding="utf-8")), "not Yu'lon's file"
    (backup,) = _backups(server_dir)
    assert backup.read_text(encoding="utf-8") == upstream, "the backup is not git's file"
    assert view.compose_banner_button.text() == controller_view_module.TUNING_RECREATE_LABEL
    assert backup.name in view.compose_banner_label.text()

    removed: list[int] = []
    started: list[int] = []
    view.services.controller.remove = lambda: removed.append(1) or True  # type: ignore[method-assign]
    view.services.controller.start = lambda: started.append(1)  # type: ignore[method-assign,assignment,return-value]
    view.compose_banner_button.click()
    assert (removed, started) == ([1], [1]), "the banner's Recreate did not recreate"
    assert view.compose_banner.isHidden(), "the banner outlived the recreate"

    rec.calls.clear()
    _, raised = _said(install_wiring.rebuild_for_app(ENTRY, server_dir)(None))
    assert raised is None, raised
    assert "build" in rec.calls and "recreate" in rec.calls, rec.calls


def test_on_a_wsl_server_the_advice_names_the_repair_in_the_distro_and_that_mends_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same failure on a server Yu'lon on Windows manages inside a distro.

    Yu'lon on Windows offers no Repair there (its engine renders for this
    host), so the sentence sends the player to the Yu'lon inside the distro,
    naming the distro and the folder's Linux path. That Yu'lon -- the wiring
    without a distro, the folder a Linux folder -- repairs it.
    """
    rec, server_dir = _wotlk_ready(tmp_path)
    _in_wsl(monkeypatch, server_dir)
    said, _ = _update_that_cannot_write_compose_back(rec, server_dir, monkeypatch)

    advice = next(line for line in said if "back on their old commits, but" in line)
    assert f"WSL distro {DISTRO}" in advice and f"({INSIDE})" in advice, advice
    assert "Yu'lon on Windows cannot" in advice, advice
    assert f"“{native.REPAIR_FILES_LABEL}” on its Server tab" in advice, advice
    assert "on the Server tab" not in advice, "it names the Windows-side tab"
    assert install_wiring.repair_compose_for_app(ENTRY, server_dir, wsl_distro=DISTRO) is None

    monkeypatch.undo()  # inside the distro, the folder is a Linux folder
    _wired_to(monkeypatch, lambda: engine(rec))
    route = install_wiring.repair_compose_for_app(ENTRY, server_dir)
    assert route is not None and route.check().state == "upstream"
    route.repair()
    assert route.check().state == "current"
    rec.calls.clear()
    _, raised = _said(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert raised is None and "build" in rec.calls, raised


def _upstream_in_place(tmp_path: Path) -> tuple[Recorder, Path, str]:
    """A finished WotLK install whose base file is the repository's own again, as git has it."""
    rec, server_dir = _wotlk_ready(tmp_path)
    base = server_dir / composegen.BASE_FILE
    base.write_text(rec.tracked[base], encoding="utf-8")
    return rec, server_dir, rec.tracked[base]


def test_the_repositorys_untouched_file_is_offered_and_replaced(tmp_path: Path) -> None:
    """The positive case the refusals below each break one rule of."""
    rec, server_dir, upstream = _upstream_in_place(tmp_path)
    made = engine(rec)
    check = made.base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "upstream" and check.added > 0, check
    repaired = made.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert repaired.backup is not None
    assert repaired.backup.read_text(encoding="utf-8") == upstream
    assert made.base_compose_check(InstallOptions(server_dir=server_dir)).state == "current"


def test_a_modified_repository_file_is_still_refused(tmp_path: Path) -> None:
    """One line changed: git says modified, so it is somebody's own file, and it stays."""
    rec, server_dir, upstream = _upstream_in_place(tmp_path)
    base = server_dir / composegen.BASE_FILE
    edited = upstream + "  # my own change\n"
    base.write_text(edited, encoding="utf-8")
    made = engine(rec)
    check = made.base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "foreign", check
    with pytest.raises(InstallerError, match="somebody's own file"):
        made.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert base.read_text(encoding="utf-8") == edited and _backups(server_dir) == []


def test_the_repositorys_file_in_a_folder_yulon_has_no_record_of_is_refused(
    tmp_path: Path,
) -> None:
    """Git's own file, untouched, but no record: a checkout somebody else set up."""
    rec, server_dir, upstream = _upstream_in_place(tmp_path)
    (server_dir / native.STATE_FILE).unlink()
    made = engine(rec)
    assert made.base_compose_check(InstallOptions(server_dir=server_dir)).state == "foreign"
    with pytest.raises(InstallerError, match="somebody's own file"):
        made.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == upstream


def test_the_repositorys_file_in_a_moved_folder_is_refused(tmp_path: Path) -> None:
    """Git's own file, untouched, with the record of another folder: its volumes are not here.

    The file names no compose project, so the record's install id is what
    says where the characters are; a Yu'lon file written for THIS folder would
    start the server as a new project beside an empty database volume.
    """
    rec, server_dir, upstream = _upstream_in_place(tmp_path)
    record = native.read_state(server_dir, valid=())
    assert record is not None
    native.write_state(server_dir, replace(record, install_id="0" * len(record.install_id)))
    made = engine(rec)
    check = made.base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "moved" and "database volume" in check.why, check
    with pytest.raises(InstallerError, match="moved or copied"):
        made.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == upstream


def _ours(rec: Recorder, server_dir: Path) -> str:
    """The compose project Yu'lon renders for this WotLK folder, by the engine's own id."""
    made = engine(rec)
    return composegen.project_name(ENTRY.id, server_dir, install_id=made._install_id(server_dir))


def test_containers_of_this_folder_under_another_project_are_refused(tmp_path: Path) -> None:
    """Codex round 2: brought up once with the repository's own stack, the characters are there.

    The record and the path say Yu'lon; the containers say `azerothcore-wotlk`.
    Writing Yu'lon's file would start the server as a new project beside an
    empty database volume, so the file stays and the sentence names both.
    """
    rec, server_dir, upstream = _upstream_in_place(tmp_path)
    rec.folder_projects[server_dir] = ("azerothcore-wotlk",) * 3
    made = engine(rec)
    check = made.base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "foreign", check
    assert "azerothcore-wotlk" in check.why and _ours(rec, server_dir) in check.why, check.why
    with pytest.raises(InstallerError, match="characters are under azerothcore-wotlk"):
        made.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == upstream
    assert _backups(server_dir) == []


def test_containers_of_this_folder_under_yulons_project_are_offered(tmp_path: Path) -> None:
    """The failed update's own case: the containers Yu'lon brought up are still there."""
    rec, server_dir, _upstream = _upstream_in_place(tmp_path)
    rec.folder_projects[server_dir] = (_ours(rec, server_dir),) * 3
    check = engine(rec).base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "upstream", check


def test_a_docker_that_will_not_say_which_containers_are_here_is_not_offered(
    tmp_path: Path,
) -> None:
    """`None` is "could not ask": a write on that answer is a write on a guess."""
    rec, server_dir, upstream = _upstream_in_place(tmp_path)
    rec.folder_projects[server_dir] = None
    made = engine(rec)
    check = made.base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "error" and "Docker would not say" in check.why, check
    with pytest.raises(InstallerError, match="Docker would not say"):
        made.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == upstream


def test_rebuilds_refusal_of_an_edited_repository_file_does_not_offer_the_repair(
    tmp_path: Path,
) -> None:
    """Cold review SF2's other half: git says modified, so the Repair would refuse it too."""
    rec, server_dir, upstream = _upstream_in_place(tmp_path)
    (server_dir / composegen.BASE_FILE).write_text(upstream + "# mine\n", encoding="utf-8")
    with pytest.raises(InstallerError) as refused:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert native.REPAIR_FILES_LABEL not in str(refused.value), refused.value
    assert "not written by Yu'lon" in str(refused.value)


def test_on_a_wsl_server_rebuilds_refusal_of_the_repositorys_file_names_the_distro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold review SF2 from Windows: the Repair is the distro's, so the refusal says so."""
    rec, server_dir, _upstream = _upstream_in_place(tmp_path)
    _in_wsl(monkeypatch, server_dir)
    with pytest.raises(InstallerError) as refused:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    said = str(refused.value)
    assert f"WSL distro {DISTRO}" in said and f"({INSIDE})" in said, said
    assert f"\u201c{native.REPAIR_FILES_LABEL}\u201d on its Server tab" in said, said
    assert "Once the reason is fixed" not in said and "another launcher" not in said, said


def test_yulons_own_wotlk_file_that_differs_is_still_left_to_update(tmp_path: Path) -> None:
    """T106's choice kept: WotLK's own file follows the app on Update, so Repair leaves it."""
    rec, server_dir = _wotlk_ready(tmp_path)
    base = server_dir / composegen.BASE_FILE
    stale = base.read_text(encoding="utf-8") + "x-mine: 1\n"
    base.write_text(stale, encoding="utf-8")
    made = engine(rec)
    check = made.base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "follows", check
    assert server_build_presses.UPDATE_TO_LATEST in check.why
    with pytest.raises(InstallerError):
        made.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert base.read_text(encoding="utf-8") == stale


def test_the_selinux_label_comes_from_the_override_not_from_the_host(tmp_path: Path) -> None:
    """Installed with `:z`; the host says no label today: the file written still carries it.

    The base file is the repository's, so it carries no label to read; the
    override beside it is Yu'lon's and does.
    """
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir, selinux_enforcing=lambda: True, fs_type=lambda path: "xfs")
    override = (server_dir / composegen.OVERRIDE_FILE).read_text(encoding="utf-8")
    assert composegen.bind_label_of(override) == ":z"
    base = server_dir / composegen.BASE_FILE
    base.write_text(rec.tracked[base], encoding="utf-8")
    made = engine(rec, selinux_enforcing=lambda: False)
    made.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert composegen.bind_label_of(base.read_text(encoding="utf-8")) == ":z"


# -- the CMaNGOS half of T163: the carried patch, its own press ----------------


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
    _built_from_old(server_dir, TBC)
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


def test_a_tbc_update_that_fails_puts_the_sources_back_and_never_writes_its_compose_files(
    tmp_path: Path,
) -> None:
    """T173: a TBC folder is not a checkout, so neither the fetch nor the put-back touches compose.

    T170's cold review (SF1) wrote a sentence for a put-back whose compose write
    failed on TBC -- reachable then only because the rewrite ran there at all,
    which was T173's defect. With the rewrite confined to WotLK, a failed press
    puts the sources and the patch back and leaves every compose file as it
    was: a player's own line survives it, and nothing is said about compose.
    """
    rec = Recorder()
    server_dir = tmp_path / "tbc"
    tbc_install(rec, server_dir, client_folder(tmp_path))
    for source in TBC.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    rec.on_clone = None
    base = server_dir / composegen.BASE_FILE
    base.write_text(base.read_text(encoding="utf-8") + "# the player's own\n", encoding="utf-8")
    kept = {name: (server_dir / name).read_bytes() for name in composegen.COMPOSE_FILES}
    stamps = {name: (server_dir / name).stat().st_mtime_ns for name in composegen.COMPOSE_FILES}

    rec.build_result = AttachedRun(2, ("error: no",))
    said, raised = _said(tbc_engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert raised is not None and native.SOURCES_PUT_BACK_NOTE in str(raised)
    assert all(rec.heads[server_dir / s.dest] == OLD for s in TBC.emulator.sources)
    assert {name: (server_dir / name).read_bytes() for name in composegen.COMPOSE_FILES} == kept
    assert {
        name: (server_dir / name).stat().st_mtime_ns for name in composegen.COMPOSE_FILES
    } == stamps
    assert not any("compose" in line for line in said if "back on their old" in line), said


# -- T170 (b): the images gone, one Rebuild press -----------------------------


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


def _tbc_images_gone(tmp_path: Path) -> tuple[Recorder, Path, Path]:
    """A finished TBC install whose images are no longer on the daemon under their tags."""
    rec = Recorder()
    server_dir = tmp_path / "tbc"
    client = client_folder(tmp_path)
    tbc_install(rec, server_dir, client)
    rec.calls.clear()
    rec.images = False
    return rec, server_dir, client


def test_reset_with_the_image_gone_names_rebuild_and_rebuild_brings_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reset → image gone → the advice → Rebuild (the app's route) compiles → Reset reads.

    One daemon double answers both questions, so the image Reset finds gone is
    one the Rebuild finds gone too: `conf_image_ref()` is among `image_refs_at()`.
    """
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    made = tbc_engine(rec)
    assert made.conf_image_ref(server_dir) in made.image_refs_at(server_dir)

    files = reset_defaults.core_files(TBC)
    copies: list[str] = []
    texts, reasons = reset_defaults.default_texts(
        TBC, server_dir, files, seams=_reset_seams(rec, copies)
    )
    assert texts == {} and set(reasons) == set(files) and copies == []
    advice = reasons[files[0]]
    assert f"Press {REBUILD} first" in advice, advice
    assert "Remove from Yu'lon" not in advice and "Install" not in advice, advice

    _wired_to(monkeypatch, lambda: tbc_engine(rec))
    said, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))
    assert raised is None, raised
    assert native.NO_ROLLBACK_KEPT in said
    assert "build" in rec.calls and "recreate" in rec.calls, rec.calls

    rec.images = True  # what the compile it just ran leaves on the daemon
    reset_defaults.default_texts(TBC, server_dir, files, seams=_reset_seams(rec, copies))
    assert copies == [made.conf_image_ref(server_dir)], "Reset still stopped short of the image"


def test_with_the_images_gone_rebuild_says_so_first_then_compiles_and_comes_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmation says it, the press says it again, and nothing is tagged or removed.

    The confirmation asks no daemon (it is composed on the GUI thread), so the
    no-rollback sentence is in every one; what matters is that the dialog the
    player says Yes to does not promise a rollback the press then cannot keep.
    """
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    assert native.no_rollback_confirmation(TBC) in rebuild_confirmation(TBC, server_dir)

    _wired_to(monkeypatch, lambda: tbc_engine(rec))
    said, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))
    assert raised is None, raised
    compiled = next(i for i, line in enumerate(said) if "compiling" in line)
    assert said.index(native.NO_ROLLBACK_KEPT) < compiled, said
    assert not [c for c in rec.calls if c.startswith(("tag:", "rmi"))], rec.calls
    assert rec.calls.index("build") < rec.calls.index("recreate"), rec.calls
    assert said[-1] == f"{TBC.name} was rebuilt and is running in {server_dir}"


def test_a_compile_that_fails_with_no_rollback_says_the_server_is_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No build was kept, nothing was replaced: the failure says so, and the record keeps it."""
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    rec.build_result = AttachedRun(2, ("error: no",))
    _wired_to(monkeypatch, lambda: tbc_engine(rec))
    _, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))
    assert raised is not None and native.NO_ROLLBACK_NOT_BUILT in str(raised), raised
    assert "recreate" not in rec.calls, rec.calls
    assert not [c for c in rec.calls if c.startswith(("tag:", "rmi"))], rec.calls
    record = native.read_state(server_dir, valid=())
    assert record is not None and native.NO_ROLLBACK_NOT_BUILT in record.last_error


def test_a_new_build_that_does_not_come_up_with_no_rollback_says_nothing_was_put_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compiled, the containers replaced, the world never ready: no restore is tried."""
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    _wired_to(monkeypatch, lambda: tbc_engine(rec, wait_ready=lambda spec, ready: False))
    _, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))
    assert raised is not None and native.NO_ROLLBACK_BUILT in str(raised), raised
    assert rec.calls.count("recreate") == 1, "a restore was attempted with nothing to restore"
    assert not [c for c in rec.calls if c.startswith(("tag:", "rmi"))], rec.calls


def test_a_finished_compile_whose_recreate_refused_says_the_containers_are_as_they_were(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compiled, then the daemon would not answer before the replace: no container moved."""
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    # The recreate's own preflight is the one `docker_ready()` a rebuild asks.
    _wired_to(monkeypatch, lambda: tbc_engine(rec, docker_ready=lambda: False))
    _, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))
    assert raised is not None and native.NO_ROLLBACK_UNTOUCHED in str(raised), raised
    assert "build" in rec.calls and "recreate" not in rec.calls, rec.calls


def test_a_rebuild_without_the_press_s_consent_still_refuses_and_names_rebuild(
    tmp_path: Path,
) -> None:
    """`update_to_latest()` calls `rebuild()` without `missing_images_ok`: that refusal remains.

    Its sentence names the one press that compiles missing images now, and
    nothing is compiled or tagged.
    """
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    with pytest.raises(InstallerError) as refused:
        list(tbc_engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    said = str(refused.value)
    assert "not all on the daemon" in said and f"Press {REBUILD} first" in said, said
    assert "Remove from Yu'lon" not in said
    assert "build" not in rec.calls and not [c for c in rec.calls if c.startswith("tag:")]


def test_on_a_wsl_server_the_rebuild_from_windows_compiles_the_missing_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuild runs for a WSL server from Windows (T125), so the same press mends it there.

    The wiring's WSL half -- the distro check, the engine built for the
    distro -- is the shipped code; the engine it builds is doubled like the rest.
    """
    rec, server_dir = _wotlk_ready(tmp_path)
    _in_wsl(monkeypatch, server_dir)
    rec.images = False
    rec.calls.clear()
    asked: list[object] = []

    def made(**kw: object) -> Any:
        return engine(rec)

    monkeypatch.setattr(
        install_wiring,
        "installer_for_app",
        lambda entry, **kw: asked.append(kw.get("wsl_distro")) or made(),
    )
    said, raised = _said(install_wiring.rebuild_for_app(ENTRY, server_dir, wsl_distro=DISTRO)(None))
    assert raised is None, raised
    assert asked == [DISTRO], "the engine was not built for the distro"
    assert native.NO_ROLLBACK_KEPT in said and "build" in rec.calls


# -- T170 round 2: a build whose names are gone, kept by its image id ----------


def _held_by_containers(rec: Recorder, server_dir: Path, image_id: str) -> str:
    """The TBC server image's name moved aside by a retag, its containers still on it."""
    made = tbc_engine(rec)
    (ref,) = made.image_refs_at(server_dir)
    project = composegen.project_name(TBC.id, server_dir, install_id=made._install_id(server_dir))
    rec.project_images[project] = (
        (TBC.container_spec().world, ref, image_id),
        (TBC.container_spec().auth, ref, image_id),
    )
    return ref


def test_an_image_moved_aside_with_its_containers_still_on_it_is_kept_as_the_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round 2: names gone is not a build gone. Kept by id, then put back by it.

    The new build never reports ready; the rollback made from the id goes back
    over the live name and the server comes up on it -- an ordinary rebuild.
    """
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    rec.ids = {"sha256:old"}
    ref = _held_by_containers(rec, server_dir, "sha256:old")
    back = ref + native.ROLLBACK_TAG_SUFFIX
    ready = iter([False, True])
    _wired_to(monkeypatch, lambda: tbc_engine(rec, wait_ready=lambda spec, r: next(ready, True)))
    said, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))

    assert f"tag:sha256:old->{back}" in rec.calls, rec.calls
    assert native.NO_ROLLBACK_KEPT not in said
    kept = next(line for line in said if line.startswith("Kept the build you have now"))
    assert "lost their names" in kept, kept
    assert rec.calls.index(f"tag:sha256:old->{back}") < rec.calls.index("build"), rec.calls
    assert raised is not None and "put back" in str(raised), raised
    assert f"tag:{back}->{ref}" in rec.calls, "the rollback made from the id was not put back"


def test_containers_whose_image_is_gone_too_leave_no_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The containers name an id the daemon no longer has: no whole build, so none is kept."""
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    _held_by_containers(rec, server_dir, "sha256:old")
    _wired_to(monkeypatch, lambda: tbc_engine(rec))
    said, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))
    assert raised is None, raised
    assert native.NO_ROLLBACK_KEPT in said
    assert not [c for c in rec.calls if c.startswith("tag:")], rec.calls


def test_containers_on_two_different_builds_leave_no_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """World and realm made from one ref but running different images: no one build to keep."""
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    rec.ids = {"sha256:old", "sha256:older"}
    ref = _held_by_containers(rec, server_dir, "sha256:old")
    project = next(iter(rec.project_images))
    rec.project_images[project] = (
        (TBC.container_spec().world, ref, "sha256:old"),
        (TBC.container_spec().auth, ref, "sha256:older"),
    )
    _wired_to(monkeypatch, lambda: tbc_engine(rec))
    said, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))
    assert raised is None, raised
    assert native.NO_ROLLBACK_KEPT in said
    assert not [c for c in rec.calls if c.startswith("tag:")], rec.calls


@pytest.mark.parametrize("to_pin", [False, True], ids=["update", "return-to-pin"])
def test_update_and_return_to_pin_still_refuse_with_the_images_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, to_pin: bool
) -> None:
    """Cold review: through the app's own Update route, not `engine.rebuild()`.

    Their confirmations promise the build you have keeps running, so neither
    compiles without a rollback; the refusal names Rebuild, the sources go back.
    """
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    for source in TBC.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    rec.on_clone = lay_patch_sources(TBC)
    _wired_to(monkeypatch, lambda: tbc_engine(rec))
    route = install_wiring.update_to_latest_for_app(TBC, server_dir)
    assert route is not None
    press = route.to_pin if to_pin else route.press
    _, raised = _said(press(None))
    assert raised is not None, "it compiled without a rollback"
    assert f"Press {REBUILD} first" in str(raised), raised
    assert native.SOURCES_PUT_BACK_NOTE in str(raised)
    assert "build" not in rec.calls and not [c for c in rec.calls if c.startswith("tag:")]


def test_the_confirmation_names_reset_to_default_only_where_it_reads_the_image() -> None:
    """Cold review NIT: WotLK's defaults are not in its image, so its Reset never says so."""
    assert "Reset to default" in native.no_rollback_confirmation(TBC)
    assert "Reset to default" not in native.no_rollback_confirmation(ENTRY)
    for entry in (TBC, ENTRY):
        said = native.no_rollback_confirmation(entry)
        assert "leave the server as it is now" in said and "has replaced the containers" in said


def test_a_docker_that_will_not_say_what_the_containers_run_refuses_the_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round 3: "could not ask" is not "no containers". Nothing is tagged or built.

    The containers may still hold the build the server runs; compiling without
    a rollback on an unanswered question would overwrite it with nothing kept.
    """
    rec, server_dir, _client = _tbc_images_gone(tmp_path)
    made = tbc_engine(rec)
    project = composegen.project_name(TBC.id, server_dir, install_id=made._install_id(server_dir))
    rec.project_images[project] = None
    _wired_to(monkeypatch, lambda: tbc_engine(rec))
    said, raised = _said(install_wiring.rebuild_for_app(TBC, server_dir)(None))
    assert raised is not None, "it compiled on an unanswered question"
    assert "Docker would not say which build" in str(raised), raised
    assert "nothing was changed" in str(raised), raised
    assert native.NO_ROLLBACK_KEPT not in said
    assert "build" not in rec.calls and not [c for c in rec.calls if c.startswith("tag:")]
