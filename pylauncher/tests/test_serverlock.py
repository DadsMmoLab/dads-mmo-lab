"""A server folder locked to this Windows account, at install and by Repair (T174).

A folder made directly under `C:\\` hands down `BUILTIN\\Users:(RX)` (measured on
yulon-win11 2026-09-28), and every secret Yu'lon writes into a server folder --
`.env`, `.db_password`, the confs -- takes what its folder hands down, because a
POSIX mode does nothing to a DACL. The owner's decision: lock the whole server
folder at install, and offer it through Repair server files… to an install made
before.

No box this suite runs on has the Win32 security API, so T151's three calls
(`winacl._user_sid`, `_read_dacl`, `_apply_dacl`) are replaced by `FakeWindows`,
as `test_winacl.py` replaces them: a DACL per folder, read back the way Windows
renders it, changed only by an apply. One thing more than T151 needs: a folder
REMOVED and made again has the DACL its parent hands down, not the one it had
(`forget()`), because WotLK's clone does exactly that to the server folder.
Everything between those calls -- the real install engines, their real stage
bodies and writers, the real Server tab -- is the code that ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder
from tests.support_native import install as install_wotlk
from tests.test_controller_view import _Ps, _services
from tests.test_families_azerothcore import as_the_clone_seam_does
from tests.test_families_cmangos import client_folder
from tests.test_families_cmangos import gated as gated  # noqa: F401 - the cmangos gate fixture
from tests.test_families_cmangos import install as install_tbc
from tests.test_playerbots_conf import CONF, SHIPPED, put_dist
from tests.test_winacl import OWNER_ONLY_AS_WINDOWS_READS_IT, USER
from yulon import platform, runner, serverlock, winacl
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import cmangos, conf
from yulon.catalog.installer import InstallerError
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import REPAIR_FILES_LABEL, ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

UNDER_C = (
    "D:AI(A;OICIID;FA;;;BA)(A;OICIID;FA;;;SY)(A;OICIID;0x1200a9;;;BU)"
    "(A;ID;0x1301bf;;;AU)(A;OICIIOID;SDGXGWGR;;;AU)"
)
"""`C:\\t174\\srv` as it was made, in SDDL: the measured `icacls` of 2026-09-28.

Administrators and SYSTEM full, `Users` read and execute, `Authenticated Users`
modify -- all inherited from `C:\\`, and no `P`."""


class FakeWindows:
    """T151's three calls, answering as Windows would, and recording what was asked."""

    def __init__(self) -> None:
        self.dacls: dict[Path, str] = {}
        self.applied: list[tuple[Path, list[str]]] = []
        self.reads: list[Path] = []
        self.refuse_apply: OSError | None = None
        self.refuse_read: OSError | None = None

    def user_sid(self) -> str:
        return USER

    def read_dacl(self, folder: Path) -> str:
        self.reads.append(folder)
        if self.refuse_read is not None:
            raise self.refuse_read
        return self.dacls.get(folder, UNDER_C)

    def apply_dacl(self, folder: Path, sddl: str) -> None:
        assert sddl == winacl.owner_only_sddl(USER), sddl
        self.applied.append((folder, sorted(p.name for p in folder.iterdir())))
        if self.refuse_apply is not None:
            raise self.refuse_apply
        self.dacls[folder] = OWNER_ONLY_AS_WINDOWS_READS_IT

    def locked(self, folder: Path) -> bool:
        return self.dacls.get(folder) == OWNER_ONLY_AS_WINDOWS_READS_IT

    def forget(self, folder: Path) -> None:
        """The folder was removed: made again, it takes what its parent hands down."""
        self.dacls.pop(folder, None)


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> FakeWindows:
    fake = FakeWindows()
    monkeypatch.setattr(winacl, "_on_windows", lambda: True)
    monkeypatch.setattr(winacl, "_user_sid", fake.user_sid)
    monkeypatch.setattr(winacl, "_read_dacl", fake.read_dacl)
    monkeypatch.setattr(winacl, "_apply_dacl", fake.apply_dacl)
    return fake


@pytest.fixture
def posix(monkeypatch: pytest.MonkeyPatch) -> FakeWindows:
    """This box as it is, with the Win32 calls replaced so that asking one is visible."""
    fake = FakeWindows()
    monkeypatch.setattr(winacl, "_on_windows", lambda: False)
    monkeypatch.setattr(winacl, "_user_sid", fake.user_sid)
    monkeypatch.setattr(winacl, "_read_dacl", fake.read_dacl)
    monkeypatch.setattr(winacl, "_apply_dacl", fake.apply_dacl)
    return fake


# -- the reading and the press ----------------------------------------------


def test_a_folder_made_under_c_reads_as_open(windows: FakeWindows, tmp_path: Path) -> None:
    assert serverlock.check(tmp_path) == serverlock.FolderLockCheck("open")


def test_a_locked_folder_reads_as_locked(windows: FakeWindows, tmp_path: Path) -> None:
    windows.dacls[tmp_path] = OWNER_ONLY_AS_WINDOWS_READS_IT
    assert serverlock.check(tmp_path) == serverlock.FolderLockCheck("locked")


def test_a_folder_that_is_not_there_is_unknown_and_asks_windows_nothing(
    windows: FakeWindows, tmp_path: Path
) -> None:
    result = serverlock.check(tmp_path / "gone")
    assert result.state == "unknown" and "is not there" in result.why
    assert windows.reads == []


def test_a_read_windows_refuses_is_unknown_and_not_an_exception(
    windows: FakeWindows, tmp_path: Path
) -> None:
    windows.refuse_read = PermissionError(5, "Access is denied")
    result = serverlock.check(tmp_path)
    assert result.state == "unknown" and "Access is denied" in result.why


def test_the_press_locks_once_and_a_second_press_changes_nothing(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """A fixture that answers differently the second time: first read open, then locked."""
    assert serverlock.lock(tmp_path) is True
    assert serverlock.lock(tmp_path) is False
    assert [folder for folder, _ in windows.applied] == [tmp_path]


def test_a_refused_press_says_windows_reason(windows: FakeWindows, tmp_path: Path) -> None:
    windows.refuse_apply = PermissionError(5, "Access is denied")
    with pytest.raises(serverlock.FolderLockError, match="Access is denied"):
        serverlock.lock(tmp_path)


def test_a_press_windows_accepted_and_did_not_keep_is_a_failure(
    windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(winacl, "_apply_dacl", lambda folder, sddl: None)
    with pytest.raises(serverlock.FolderLockError, match="still reads"):
        serverlock.lock(tmp_path)


@pytest.mark.parametrize("game", sorted(game.id for game in load_catalog().games))
def test_on_windows_every_game_is_offered_the_lock(
    windows: FakeWindows, tmp_path: Path, game: str
) -> None:
    entry = load_catalog().get(game)
    if entry.install.password.file:
        (tmp_path / entry.install.password.file).write_text("hunter2", encoding="utf-8")
    route = ControllerServices.for_entry(entry, tmp_path).lock_folder
    assert route is not None and route.folder == tmp_path


def test_a_server_inside_a_wsl_distro_is_not_offered_the_lock(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """Its folder's permissions are the distro's; a Windows DACL is not what guards it."""
    assert serverlock.route_for_app(tmp_path, wsl_distro="Ubuntu") is None
    assert ControllerServices.for_entry(ENTRY, tmp_path, wsl_distro="Ubuntu").lock_folder is None
    assert windows.reads == []


def test_off_windows_nothing_is_offered_and_nothing_asked(
    posix: FakeWindows, tmp_path: Path
) -> None:
    assert serverlock.route_for_app(tmp_path) is None
    assert ControllerServices.for_entry(ENTRY, tmp_path).lock_folder is None
    assert serverlock.check(tmp_path).state == "unknown"
    assert list(serverlock.InstallLock(tmp_path).ensure()) == []
    assert posix.reads == [] and posix.applied == []


# -- the install -------------------------------------------------------------


def _watch_secrets(
    monkeypatch: pytest.MonkeyPatch, fake: FakeWindows, server_dir: Path
) -> list[tuple[str, bool]]:
    """Every secret writer inside the server folder, recording whether the folder was locked.

    The three that write the database password during an install, wrapped
    where their callers look them up; each still writes.
    """
    seen: list[tuple[str, bool]] = []

    def wrap(module: object, name: str) -> None:
        real = getattr(module, name)

        def watched(path: Path, *args: object, **kwargs: object) -> object:
            assert path.is_relative_to(server_dir), path
            seen.append((path.relative_to(server_dir).as_posix(), fake.locked(server_dir)))
            return real(path, *args, **kwargs)

        monkeypatch.setattr(module, name, watched)

    wrap(platform, "write_private_atomically")
    wrap(cmangos, "_write_secret")
    wrap(conf, "_write")
    return seen


def test_a_tbc_install_locks_the_folder_before_its_first_secret(
    windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first apply finds only the claim; `.db_password`, `.env` and the confs land after it."""
    server_dir = tmp_path / "srv"
    seen = _watch_secrets(monkeypatch, windows, server_dir)
    lines = install_tbc(Recorder(), server_dir, client_folder(tmp_path))
    assert windows.applied[0] == (server_dir, [".yulon-install.json"])
    assert len(windows.applied) == 1, "a folder that stayed locked was locked again"
    names = [name for name, _ in seen]
    assert ".db_password" in names and ".env" in names, names
    assert any(name.endswith(".conf") for name in names), names
    assert all(locked for _, locked in seen), seen
    said = serverlock.LOCKED_LINE.format(folder=server_dir)
    assert lines.count(said) == 1
    assert lines.index(said) < lines.index("--- patch-sources")


def test_a_wotlk_install_locks_again_the_folder_its_clone_made_anew(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """`clone-core` clones INTO the server folder, and the seam first removes it.

    So the folder locked before stage one is gone, and the one the clone leaves
    takes the DACL `C:\\` hands down. Locked again before the next stage, and
    said once. (WotLK's password is the catalog's, so its install writes no
    `.env`; the command channel writes one later, into the locked folder.)
    """
    server_dir = tmp_path / "wow"
    rec = Recorder()
    recorders_clone = rec.seams().clone

    def clone(spec: object) -> None:
        dest = spec.dest  # type: ignore[attr-defined]
        existed = dest.is_dir() and not (dest / ".git").is_dir()
        as_the_clone_seam_does(dest)
        if existed:
            windows.forget(dest)
        recorders_clone(spec)  # type: ignore[arg-type]

    lines = install_wotlk(rec, server_dir, clone=clone)
    assert [folder for folder, _ in windows.applied] == [server_dir, server_dir]
    assert ".git" in windows.applied[1][1], "the second lock was not over the clone"
    assert windows.locked(server_dir), "the folder the clone made was left as C:\\ hands it down"
    assert lines.count(serverlock.LOCKED_LINE.format(folder=server_dir)) == 1


def test_an_install_into_somebody_s_own_checkout_leaves_its_permissions_alone(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """The one folder a stage meets that is not ours: refused by the clone stage, untouched."""
    server_dir = tmp_path / "wow"
    (server_dir / ".git").mkdir(parents=True)
    rec = Recorder()
    rec.remotes[server_dir] = "https://github.com/someone/else.git"
    with pytest.raises(InstallerError, match="already a git checkout"):
        install_wotlk(rec, server_dir)
    assert windows.applied == [] and windows.reads == []


def test_a_lock_windows_refuses_is_said_once_and_the_install_carries_on(
    windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = tmp_path / "srv"
    windows.refuse_apply = PermissionError(5, "Access is denied")
    seen = _watch_secrets(monkeypatch, windows, server_dir)
    lines = install_tbc(Recorder(), server_dir, client_folder(tmp_path))
    warned = [line for line in lines if line.startswith("Could not lock ")]
    assert len(warned) == 1 and "Access is denied" in warned[0], warned
    assert "Repair server files…" in warned[0]
    assert len(windows.applied) == 1, "a refusing folder was asked again at every stage"
    assert ".db_password" in [name for name, _ in seen]
    assert (server_dir / ".db_password").is_file()
    assert lines[-1].endswith(f"is installed and running in {server_dir}")


def test_off_windows_an_install_asks_nothing_about_a_dacl(
    posix: FakeWindows, tmp_path: Path
) -> None:
    server_dir = tmp_path / "srv"
    lines = install_tbc(Recorder(), server_dir, client_folder(tmp_path))
    assert posix.reads == [] and posix.applied == []
    assert not [line for line in lines if "lock" in line.lower() and "Windows" in line]


# -- Repair server files… on the Server tab ----------------------------------


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(ps: _Ps, server_dir: Path) -> ControllerView:
    """A WotLK tab whose module conf is there, so the lock is the one thing owed."""
    put_dist(server_dir)
    (server_dir / CONF).write_bytes(SHIPPED)
    services = _services(ps, server_dir, [])
    services.lock_folder = serverlock.route_for_app(server_dir)
    return ControllerView(ENTRY, services, status_poll_ms=0)


def _answer(monkeypatch: pytest.MonkeyPatch, yes: bool) -> list[str]:
    asked: list[str] = []
    from PySide6.QtWidgets import QMessageBox

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> int:
        asked.append(text)
        button = QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
        return int(button.value)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def test_an_open_folder_shows_the_repair_banner_naming_it(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path)
    assert not view.compose_banner.isHidden()
    assert view.compose_banner_button.text() == REPAIR_FILES_LABEL
    assert str(tmp_path) in view.compose_banner_label.text()
    assert windows.applied == [], "the tab changed the folder before anybody pressed"


def test_a_locked_folder_shows_no_banner(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path
) -> None:
    windows.dacls[tmp_path] = OWNER_ONLY_AS_WINDOWS_READS_IT
    assert _view(ps, tmp_path).compose_banner.isHidden()


def test_pressing_asks_first_and_no_changes_nothing(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _view(ps, tmp_path)
    asked = _answer(monkeypatch, yes=False)
    view.compose_banner_button.click()
    assert len(asked) == 1 and str(tmp_path) in asked[0]
    assert "nothing needs restarting" in asked[0]
    assert windows.applied == []


def test_yes_locks_the_folder_and_the_banner_goes(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _view(ps, tmp_path)
    _answer(monkeypatch, yes=True)
    view.compose_banner_button.click()
    assert [folder for folder, _ in windows.applied] == [tmp_path]
    assert windows.locked(tmp_path)
    assert view.compose_banner.isHidden()
    assert view.problem_label.text() == controller_view_module.LOCK_FOLDER_DONE.format(
        folder=tmp_path
    )


def test_a_refused_lock_says_why_and_the_offer_stays(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _view(ps, tmp_path)
    windows.refuse_apply = PermissionError(5, "Access is denied")
    _answer(monkeypatch, yes=True)
    view.compose_banner_button.click()
    assert "was not locked" in view.problem_label.text()
    assert "Access is denied" in view.problem_label.text()
    assert not view.compose_banner.isHidden()
    assert view.compose_banner_button.isEnabled()


def test_a_missing_module_conf_is_offered_before_the_lock(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One button, two things owed: a press mends the first; the check after it offers the next."""
    from yulon.install_wiring import repair_confs_for_app

    put_dist(tmp_path)
    services = _services(ps, tmp_path, [])
    services.repair_confs = repair_confs_for_app(ENTRY, tmp_path)
    services.lock_folder = serverlock.route_for_app(tmp_path)
    view = ControllerView(ENTRY, services, status_poll_ms=0)
    assert "playerbots.conf" in view.compose_banner_label.text()
    _answer(monkeypatch, yes=True)
    view.compose_banner_button.click()
    assert (tmp_path / CONF).read_bytes() == SHIPPED
    assert windows.applied == [], "the conf's press locked the folder too"
