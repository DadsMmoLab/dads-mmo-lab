"""A server folder locked to this Windows account, at install and by Repair (T174).

A folder made directly under `C:\\` hands down `BUILTIN\\Users:(RX)` (measured on
yulon-win11 2026-09-28), and every secret Yu'lon writes into a server folder --
`.env`, `.db_password`, the confs -- takes what its folder hands down, because a
POSIX mode does nothing to a DACL. The owner's decision: lock the whole server
folder at install, and offer it through Repair server files… to an install made
before. The lead's, round 2: the install narrows only Windows' broad default
groups on its own, and Repair offers the lock only to a folder somebody else can
read, naming them.

No box this suite runs on has the Win32 security API, so T151's calls
(`winacl._user_sid`, `_sid_of`, `_read_dacl`, `_apply_dacl`) and this module's
`_account_name` are replaced by `FakeWindows`, as `test_winacl.py` replaces them:
a DACL per folder, read back the way Windows renders it (aliases, `PAI`), changed
only by an apply. One thing more than T151 needs: a folder REMOVED and made again
has the DACL its parent hands down, not the one it had (`forget()`), because
WotLK's clone does exactly that to the server folder. Everything between those
calls -- the real install engines, their real stage bodies and writers, the real
Server tab -- is the code that ships.
"""

from __future__ import annotations

from collections.abc import Callable
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
from tests.test_winacl import BUILT_IN_ADMINISTRATOR, PROFILE_DEFAULT, SDDL_ALIASES, USER
from yulon import docker, platform, runner, serverlock, winacl
from yulon.catalog import native
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

BOB = "S-1-5-21-4444444444-5555555555-6666666666-1002"
"""A second local account on this PC, somebody a person gave the folder to."""

ALIASES = {**SDDL_ALIASES, "AU": "S-1-5-11", "WD": "S-1-1-0", "IU": "S-1-5-4", "CO": "S-1-3-0"}
"""What `ConvertStringSidToSidW` answers for every alias these DACLs carry."""

NAMES = {
    USER: "PC\\pk",
    BOB: "PC\\bob",
    BUILT_IN_ADMINISTRATOR: "PC\\Administrator",
    "S-1-5-32-545": "BUILTIN\\Users",
    "S-1-5-11": "NT AUTHORITY\\Authenticated Users",
}
"""What `LookupAccountSidW` answers here; a SID it has no name for makes it fail."""


def sid_of(trustee: str) -> str:
    if trustee.startswith("S-"):
        return trustee
    if trustee not in ALIASES:
        raise OSError(1337, f"The security ID structure is invalid: {trustee}")
    return ALIASES[trustee]


def account_name(sid: str) -> str:
    if sid not in NAMES:
        raise OSError(1332, "No mapping between account names and security IDs was done")
    return NAMES[sid]


def rendered(sid: str) -> str:
    """How Windows spells a trustee in SDDL: its alias if it has one."""
    return next((alias for alias, known in ALIASES.items() if known == sid), sid)


class FakeWindows:
    """The Win32 calls, answering as Windows would, and recording what was asked."""

    def __init__(self, *, user: str = USER) -> None:
        self.user = user
        self.initial = UNDER_C
        self.dacls: dict[Path, str] = {}
        self.applied: list[tuple[Path, list[str]]] = []
        self.reads: list[Path] = []
        self.refusals: list[OSError] = []
        """What the next applies RAISE, one each, before they start to succeed."""
        self.refuse_read: OSError | None = None
        self.read_failures: list[OSError] = []
        """What the next reads RAISE, one each, before they start to answer."""
        self.generation: dict[Path, int] = {}
        """How many times each folder was made anew: what its NTFS file id tells apart."""

    @property
    def owner_only(self) -> str:
        who = rendered(self.user)
        return f"D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{who})"

    def user_sid(self) -> str:
        return self.user

    def read_dacl(self, folder: Path) -> str:
        self.reads.append(folder)
        if self.refuse_read is not None:
            raise self.refuse_read
        if self.read_failures:
            raise self.read_failures.pop(0)
        return self.dacls.get(folder, self.initial)

    def apply_dacl(self, folder: Path, sddl: str) -> None:
        assert sddl == winacl.owner_only_sddl(self.user), sddl
        self.applied.append((folder, sorted(p.name for p in folder.iterdir())))
        if self.refusals:
            raise self.refusals.pop(0)
        self.dacls[folder] = self.owner_only

    def locked(self, folder: Path) -> bool:
        return self.dacls.get(folder) == self.owner_only

    def forget(self, folder: Path) -> None:
        """The folder was removed: made again, it takes what its parent hands down.

        And it is another folder, with another file id, though the path is the
        same: which a Linux inode, reused at once, does not reliably show.
        """
        self.dacls.pop(folder, None)
        self.generation[folder] = self.generation.get(folder, 0) + 1

    def identity(self, folder: Path) -> tuple[int, int]:
        return (self.generation.get(folder, 0), hash(folder))


def _stand_in(monkeypatch: pytest.MonkeyPatch, fake: FakeWindows, on_windows: bool) -> None:
    monkeypatch.setattr(winacl, "_on_windows", lambda: on_windows)
    monkeypatch.setattr(winacl, "_user_sid", fake.user_sid)
    monkeypatch.setattr(winacl, "_read_dacl", fake.read_dacl)
    monkeypatch.setattr(winacl, "_apply_dacl", fake.apply_dacl)
    monkeypatch.setattr(winacl, "_sid_of", sid_of)
    monkeypatch.setattr(serverlock, "_account_name", account_name)
    monkeypatch.setattr(serverlock, "_identity", fake.identity)


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> FakeWindows:
    fake = FakeWindows()
    _stand_in(monkeypatch, fake, on_windows=True)
    return fake


@pytest.fixture
def posix(monkeypatch: pytest.MonkeyPatch) -> FakeWindows:
    """This box as it is, with the Win32 calls replaced so that asking one is visible."""
    fake = FakeWindows()
    _stand_in(monkeypatch, fake, on_windows=False)
    return fake


# -- who can read a folder -----------------------------------------------------


def test_a_folder_made_under_c_is_exposed_to_users_and_authenticated_users(
    windows: FakeWindows, tmp_path: Path
) -> None:
    assert serverlock.check(tmp_path) == serverlock.FolderLockCheck(
        "exposed", readers=("BUILTIN\\Users", "NT AUTHORITY\\Authenticated Users"), chosen=()
    )


def test_a_folder_under_the_profile_is_private_and_offers_nothing(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """The three, inherited and not protected: not the owner-only DACL, and nobody else reads."""
    windows.initial = PROFILE_DEFAULT
    assert serverlock.check(tmp_path) == serverlock.FolderLockCheck("private")


def test_a_locked_folder_reads_as_locked(windows: FakeWindows, tmp_path: Path) -> None:
    windows.dacls[tmp_path] = windows.owner_only
    assert serverlock.check(tmp_path) == serverlock.FolderLockCheck("locked")


def test_a_second_named_account_is_exposed_and_named_as_chosen(
    windows: FakeWindows, tmp_path: Path
) -> None:
    windows.initial = f"{PROFILE_DEFAULT}(A;OICIID;FR;;;{BOB})"
    assert serverlock.check(tmp_path) == serverlock.FolderLockCheck(
        "exposed", readers=("PC\\bob",), chosen=("PC\\bob",)
    )


@pytest.mark.parametrize(
    ("entry", "reads"),
    [
        # Each differs from the profile's default by ONE entry for `Users`.
        ("(A;OICIID;0x1200a9;;;BU)", True),  # read and execute
        ("(A;OICIIOID;GR;;;BU)", True),  # generic read, for what is made inside only
        ("(A;OICIID;CC;;;BU)", True),  # FILE_READ_DATA alone
        ("(A;OICIID;FX;;;BU)", False),  # traverse and execute, no content
        ("(A;OICIID;0x1200a0;;;BU)", False),  # the same, as a number
        ("(D;OICI;FA;;;BU)", False),  # a deny takes access away
        ("(A;OICIID;ZZ;;;BU)", True),  # a right nobody knows is read as a read
        ("(A;OICIID;FA;;;CO)", False),  # CREATOR OWNER is whoever owns the file
    ],
)
def test_what_counts_as_somebody_else_reading(
    windows: FakeWindows, tmp_path: Path, entry: str, reads: bool
) -> None:
    windows.initial = PROFILE_DEFAULT + entry
    assert serverlock.check(tmp_path).state == ("exposed" if reads else "private")


def test_an_entry_windows_cannot_name_is_somebody_else(
    windows: FakeWindows, tmp_path: Path
) -> None:
    windows.initial = f"{PROFILE_DEFAULT}(A;OICIID;FR;;;XX)"
    result = serverlock.check(tmp_path)
    assert result.state == "exposed" and result.chosen == ("XX",)


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


# -- the press -----------------------------------------------------------------


def test_the_press_locks_once_and_a_second_press_changes_nothing(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """A fixture that answers differently the second time: first read open, then locked."""
    assert serverlock.lock(tmp_path) is True
    assert serverlock.lock(tmp_path) is False
    assert [folder for folder, _ in windows.applied] == [tmp_path]


def test_the_built_in_administrator_s_folder_reads_as_locked_after_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows spells that account `LA` in the read-back, and only `_sid_of` knows it (SF3)."""
    fake = FakeWindows(user=BUILT_IN_ADMINISTRATOR)
    _stand_in(monkeypatch, fake, on_windows=True)
    assert "LA" in fake.owner_only
    assert serverlock.lock(tmp_path) is True
    assert serverlock.check(tmp_path).state == "locked"
    assert serverlock.lock(tmp_path) is False
    assert len(fake.applied) == 1


def test_a_refused_press_says_windows_reason(windows: FakeWindows, tmp_path: Path) -> None:
    windows.refusals = [PermissionError(5, "Access is denied")]
    with pytest.raises(serverlock.FolderLockRefused, match="Access is denied"):
        serverlock.lock(tmp_path)


def test_a_read_that_fails_is_not_a_refusal(windows: FakeWindows, tmp_path: Path) -> None:
    windows.read_failures = [OSError(21, "The device is not ready")]
    with pytest.raises(serverlock.FolderLockError, match="not ready") as failed:
        serverlock.lock(tmp_path)
    assert not isinstance(failed.value, serverlock.FolderLockRefused)
    assert windows.applied == []


def test_a_press_windows_accepted_and_did_not_keep_is_a_failure(
    windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(winacl, "_apply_dacl", lambda folder, sddl: None)
    with pytest.raises(serverlock.FolderLockRefused, match="still reads"):
        serverlock.lock(tmp_path)


# -- where it is offered -------------------------------------------------------


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


# -- the install ---------------------------------------------------------------


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


def _faithful_clone(rec: Recorder, fake: FakeWindows) -> Callable[..., None]:
    """The Recorder's clone, opened as both real seams open: a non-git destination is removed."""
    recorders_clone = rec.seams().clone

    def clone(spec: object) -> None:
        dest = spec.dest  # type: ignore[attr-defined]
        existed = dest.is_dir() and not (dest / ".git").is_dir()
        as_the_clone_seam_does(dest)
        if existed:
            fake.forget(dest)
        recorders_clone(spec)  # type: ignore[arg-type]

    return clone


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


def test_a_resume_of_an_install_from_before_t174_locks_the_folder_it_finds(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """The folder holds its record and an earlier run's secrets; the first stage locks it."""
    server_dir = tmp_path / "srv"
    client = client_folder(tmp_path)
    rec = Recorder(build_result=docker.AttachedRun(1, ("boom",)))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(winacl, "_on_windows", lambda: False)
    try:
        with pytest.raises(InstallerError):
            install_tbc(rec, server_dir, client)
    finally:
        monkeypatch.undo()
    assert (server_dir / ".db_password").is_file() and windows.applied == []
    rec.build_result = docker.AttachedRun(0, ("built",))
    install_tbc(rec, server_dir, client)
    assert windows.applied[0][0] == server_dir
    assert ".db_password" in windows.applied[0][1]
    assert windows.locked(server_dir)


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
    lines = install_wotlk(rec, server_dir, clone=_faithful_clone(rec, windows))
    assert [folder for folder, _ in windows.applied] == [server_dir, server_dir]
    assert ".git" in windows.applied[1][1], "the second lock was not over the clone"
    assert windows.locked(server_dir), "the folder the clone made was left as C:\\ hands it down"
    assert lines.count(serverlock.LOCKED_LINE.format(folder=server_dir)) == 1


def test_a_lock_refused_before_the_clone_is_asked_again_and_lands_before_generate_compose(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """Codex, round 2: a refusal is said once and never ends the asking."""
    server_dir = tmp_path / "wow"
    rec = Recorder()
    windows.refusals = [PermissionError(5, "Access is denied")]
    lines = install_wotlk(rec, server_dir, clone=_faithful_clone(rec, windows))
    assert [folder for folder, _ in windows.applied] == [server_dir, server_dir]
    assert windows.locked(server_dir)
    warned = [line for line in lines if line.startswith("Could not lock ")]
    assert len(warned) == 1 and "Access is denied" in warned[0], warned
    locked = serverlock.LOCKED_LINE.format(folder=server_dir)
    assert lines.index(warned[0]) < lines.index("--- clone-modules") < lines.index(locked)
    assert lines.index(locked) < lines.index("--- generate-compose")


def test_a_lock_windows_always_refuses_is_said_once_tried_once_and_the_install_carries_on(
    windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 3 (Codex): the same folder is not walked again at every stage to refuse again."""
    server_dir = tmp_path / "srv"
    windows.refusals = [PermissionError(5, "Access is denied")] * 100
    seen = _watch_secrets(monkeypatch, windows, server_dir)
    lines = install_tbc(Recorder(), server_dir, client_folder(tmp_path))
    warned = [line for line in lines if line.startswith("Could not lock ")]
    assert len(warned) == 1 and "Access is denied" in warned[0], warned
    assert "Repair server files…" in warned[0]
    assert len(windows.applied) == 1, "the folder that refused was asked again"
    assert ".db_password" in [name for name, _ in seen]
    assert (server_dir / ".db_password").is_file()
    assert lines[-1].endswith(f"is installed and running in {server_dir}")


def test_a_refusal_is_remembered_for_that_folder_and_forgotten_when_the_folder_is_replaced(
    windows: FakeWindows, tmp_path: Path
) -> None:
    folder = tmp_path / "srv"
    folder_lock = serverlock.InstallLock(folder)
    assert list(folder_lock.ensure()) == [], "a folder that is not there was asked about"
    folder.mkdir()
    windows.refusals = [PermissionError(5, "Access is denied")] * 2
    assert len(list(folder_lock.ensure())) == 1
    assert list(folder_lock.ensure()) == [] and len(windows.applied) == 1
    windows.forget(folder)
    assert list(folder_lock.ensure()) == [], "the second refusal was said again"
    assert len(windows.applied) == 2, "the folder made anew was not tried"
    windows.forget(folder)
    assert list(folder_lock.ensure()) == [serverlock.LOCKED_LINE.format(folder=folder)]
    assert windows.locked(folder)
    reads = len(windows.reads)
    assert list(folder_lock.ensure()) == [] and len(windows.applied) == 3
    assert len(windows.reads) == reads + 1, "a lock that held was not checked with one read"


def test_a_read_that_failed_once_is_asked_again_on_the_same_folder_before_the_first_secret(
    windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 4 (Codex): only a refused apply is remembered; a read is cheap and asked again."""
    server_dir = tmp_path / "srv"
    windows.read_failures = [OSError(21, "The device is not ready")]
    seen = _watch_secrets(monkeypatch, windows, server_dir)
    lines = install_tbc(Recorder(), server_dir, client_folder(tmp_path))
    assert [folder for folder, _ in windows.applied] == [server_dir]
    assert windows.generation.get(server_dir, 0) == 0, "the folder was not the same one"
    warned = [line for line in lines if line.startswith("Could not lock ")]
    assert len(warned) == 1 and "not ready" in warned[0], warned
    locked = serverlock.LOCKED_LINE.format(folder=server_dir)
    assert lines.index(warned[0]) < lines.index("--- patch-sources") < lines.index(locked)
    assert lines.index(locked) < lines.index("--- db-password")
    assert ".db_password" in [name for name, _ in seen]
    assert all(was_locked for _, was_locked in seen), seen


def test_a_folder_s_identity_is_the_folder_not_the_path(tmp_path: Path) -> None:
    """The real `_identity`: the same folder answers the same; another folder answers otherwise."""
    one, other = tmp_path / "one", tmp_path / "other"
    one.mkdir()
    other.mkdir()
    assert serverlock._identity(one) == serverlock._identity(one)
    assert serverlock._identity(one) != serverlock._identity(other)


def test_a_folder_that_grants_a_named_account_is_left_as_it_is_and_said_so(
    windows: FakeWindows, tmp_path: Path
) -> None:
    """Somebody gave `bob` this folder; the install does not take that away on its own."""
    server_dir = tmp_path / "srv"
    windows.initial = f"{UNDER_C}(A;OICIID;FR;;;{BOB})"
    lines = install_tbc(Recorder(), server_dir, client_folder(tmp_path))
    assert windows.applied == []
    left = [line for line in lines if "leaves its permissions as they are" in line]
    assert len(left) == 1 and "PC\\bob" in left[0] and "Users" not in left[0], left
    assert lines[-1].endswith(f"is installed and running in {server_dir}")


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


def test_off_windows_an_install_asks_nothing_about_a_dacl(
    posix: FakeWindows, tmp_path: Path
) -> None:
    server_dir = tmp_path / "srv"
    lines = install_tbc(Recorder(), server_dir, client_folder(tmp_path))
    assert posix.reads == [] and posix.applied == []
    assert not [line for line in lines if "lock" in line.lower() and "Windows" in line]


# -- Repair server files… on the Server tab ------------------------------------


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(ps: _Ps, server_dir: Path, **routes: object) -> ControllerView:
    """A WotLK tab whose module conf is there, so only what `routes` wire is owed."""
    put_dist(server_dir)
    (server_dir / CONF).write_bytes(SHIPPED)
    services = _services(ps, server_dir, [])
    services.lock_folder = serverlock.route_for_app(server_dir)
    for name, route in routes.items():
        setattr(services, name, route)
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


def test_an_exposed_folder_shows_the_banner_naming_who_can_read_it(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path)
    assert not view.compose_banner.isHidden()
    assert view.compose_banner_button.text() == REPAIR_FILES_LABEL
    said = view.compose_banner_label.text()
    assert str(tmp_path) in said
    assert "BUILTIN\\Users and NT AUTHORITY\\Authenticated Users can read" in said, said
    assert windows.applied == [], "the tab changed the folder before anybody pressed"


@pytest.mark.parametrize("dacl", ["locked", PROFILE_DEFAULT])
def test_a_locked_or_private_folder_shows_no_banner(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path, dacl: str
) -> None:
    windows.dacls[tmp_path] = windows.owner_only if dacl == "locked" else dacl
    assert _view(ps, tmp_path).compose_banner.isHidden()


def test_pressing_asks_first_names_the_readers_and_no_changes_nothing(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _view(ps, tmp_path)
    asked = _answer(monkeypatch, yes=False)
    view.compose_banner_button.click()
    assert len(asked) == 1 and str(tmp_path) in asked[0]
    assert "Today BUILTIN\\Users and NT AUTHORITY\\Authenticated Users can read" in asked[0]
    assert "nothing needs restarting" in asked[0]
    assert "given back by hand" not in asked[0], "a broad group was named as somebody's choice"
    assert windows.applied == []


def test_the_question_names_a_chosen_account_that_would_lose_access(
    qapp: object, ps: _Ps, windows: FakeWindows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows.initial = f"{UNDER_C}(A;OICIID;FR;;;{BOB})"
    view = _view(ps, tmp_path)
    asked = _answer(monkeypatch, yes=False)
    view.compose_banner_button.click()
    assert "PC\\bob has access to this folder that somebody gave" in asked[0], asked[0]
    assert "loses it too" in asked[0]


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
    windows.refusals = [PermissionError(5, "Access is denied")]
    _answer(monkeypatch, yes=True)
    view.compose_banner_button.click()
    assert "was not locked" in view.problem_label.text()
    assert "Access is denied" in view.problem_label.text()
    assert not view.compose_banner.isHidden()
    assert view.compose_banner_button.isEnabled()


@pytest.mark.parametrize("state", ["stale", "upstream"])
def test_a_compose_file_on_offer_is_repaired_before_the_folder_is_locked(
    qapp: object,
    ps: _Ps,
    windows: FakeWindows,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """Cold review, round 2: `stale`, and since T170 `upstream`, press the compose repair."""
    repaired: list[str] = []

    def repair() -> native.ComposeRepaired:
        repaired.append(state)
        return native.ComposeRepaired(path=tmp_path / "docker-compose.yml", backup=None)

    route = native.ComposeRepairRoute(
        check=lambda: native.ComposeCheck(state, added=1, removed=1),  # type: ignore[arg-type]
        repair=repair,
    )
    view = _view(ps, tmp_path, repair_compose=route)
    assert "docker-compose.yml" in view.compose_banner_label.text()
    _answer(monkeypatch, yes=True)
    view.compose_banner_button.click()
    assert repaired == [state]
    assert windows.applied == [], "the compose file's press locked the folder instead"


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
