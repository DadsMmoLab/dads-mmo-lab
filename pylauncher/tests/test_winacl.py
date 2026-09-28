"""An owner-only DACL on the folders Yu'lon keeps its secrets in, on Windows (T151).

A POSIX mode does nothing to a Windows DACL (measured by K.3 on 2026-09-01:
`os.open(..., 0o600)` leaves the ACL byte-identical), so the command channel's
GM password and a kept database password took whatever `%APPDATA%` gave them.

No box this suite runs on has the Win32 security API, so the three calls that
reach it (`_user_sid`, `_read_dacl`, `_apply_dacl`) are replaced by `FakeWindows`,
which keeps a DACL per folder the way Windows does: read back in the form
Windows renders it (aliases, `PAI`), changed only by an apply. Everything
between those calls -- what is asked for, when, what counts as already
owner-only, what a failure does to the write -- is the real code on the real
write path.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from yulon import channel_setup, dbsecret, winacl

USER = "S-1-5-21-1111111111-2222222222-3333333333-1001"
"""A local account's SID, as `ConvertSidToStringSidW` spells one."""

OWNER_ONLY_AS_WINDOWS_READS_IT = f"D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{USER})"
"""What `ConvertSecurityDescriptorToStringSecurityDescriptorW` answers after the apply.

Aliases for the well-known SIDs, and `AI` beside `P`: `SetNamedSecurityInfoW`
marks the DACL auto-inherited. So the check cannot be a string comparison with
what was asked for."""

PROFILE_DEFAULT = f"D:AI(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;FA;;;{USER})"
"""`%APPDATA%\\yulon` in an ordinary profile: the same three, inherited, not protected."""

LOOSENED = f"{PROFILE_DEFAULT}(A;OICIID;0x1200a9;;;BU)"
"""The ticket's case: a profile whose ACL also gives `Users` read (`0x1200a9` = RX)."""

PASSWORD = "a-password-nobody-should-see"


class FakeWindows:
    """The three Win32 calls, answering as Windows would, and recording what was asked."""

    def __init__(self, *, initial: str = LOOSENED) -> None:
        self.initial = initial
        self.dacls: dict[Path, str] = {}
        self.applied: list[tuple[Path, str, list[str]]] = []
        self.reads: list[Path] = []
        self.refuse_apply: OSError | None = None
        self.apply_does_not_stick = False

    def user_sid(self) -> str:
        return USER

    def read_dacl(self, folder: Path) -> str:
        self.reads.append(folder)
        return self.dacls.get(folder, self.initial)

    def apply_dacl(self, folder: Path, sddl: str) -> None:
        # What was already in the folder when its DACL changed: a secret that
        # landed before this call took the old, inherited ACL.
        self.applied.append((folder, sddl, sorted(p.name for p in folder.iterdir())))
        if self.refuse_apply is not None:
            raise self.refuse_apply
        if not self.apply_does_not_stick:
            self.dacls[folder] = OWNER_ONLY_AS_WINDOWS_READS_IT


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> FakeWindows:
    fake = FakeWindows()
    monkeypatch.setattr(winacl, "_on_windows", lambda: True)
    monkeypatch.setattr(winacl, "_user_sid", fake.user_sid)
    monkeypatch.setattr(winacl, "_read_dacl", fake.read_dacl)
    monkeypatch.setattr(winacl, "_apply_dacl", fake.apply_dacl)
    return fake


def _save_credential(config_dir: Path) -> Path:
    return channel_setup.save_credential(
        channel_setup.Verified(account="YULON_AB12CD34", password=PASSWORD),
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=config_dir,
    )


# -- what is asked for -------------------------------------------------------


def test_the_dacl_asked_for_is_protected_and_names_system_administrators_and_this_account() -> None:
    """`P` turns inheritance from `%APPDATA%` off; `OICI` hands the three to every file inside.

    Administrators stays (`winacl.trustees`): the profile's own default has it,
    an administrator can take ownership of the folder whatever it says, and
    removing it costs backup tools and an elevated Yu'lon their access.
    """
    assert winacl.owner_only_sddl(USER) == (
        "D:P(A;OICI;FA;;;S-1-5-18)(A;OICI;FA;;;S-1-5-32-544)" f"(A;OICI;FA;;;{USER})"
    )


def test_the_dacl_as_windows_reads_it_back_counts_as_owner_only() -> None:
    assert winacl.is_owner_only(OWNER_ONLY_AS_WINDOWS_READS_IT, USER)


def test_full_control_spelt_as_a_number_is_still_full_control() -> None:
    sddl = f"D:P(A;OICI;0x1f01ff;;;SY)(A;CIOI;FA;;;BA)(A;OICI;FA;;;{USER})"
    assert winacl.is_owner_only(sddl, USER)


@pytest.mark.parametrize(
    "sddl",
    [
        # Inheritance still on: the one rule broken is the missing `P`.
        f"D:AI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{USER})",
        # A fourth trustee: `Users` may read.
        f"D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{USER})(A;OICI;0x1200a9;;;BU)",
        # This account is missing, so the app could lock itself out of its own file.
        "D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
        # This account's ACE is not handed down: a file created inside would not get it.
        f"D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;;FA;;;{USER})",
        # Less than full control for this account.
        f"D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;{USER})",
        # A deny in place of an allow.
        f"D:PAI(A;OICI;FA;;;SY)(D;OICI;FA;;;BA)(A;OICI;FA;;;{USER})",
        # The profile's default: three inherited ACEs and no `P`.
        PROFILE_DEFAULT,
    ],
)
def test_a_dacl_that_breaks_one_rule_is_not_owner_only(sddl: str) -> None:
    assert not winacl.is_owner_only(sddl, USER)


# -- when it is asked for ----------------------------------------------------


def test_a_loose_folder_is_narrowed_once_and_then_left_alone(
    tmp_path: Path, windows: FakeWindows, caplog: pytest.LogCaptureFixture
) -> None:
    """The second call finds the DACL the first one set and changes nothing."""
    with caplog.at_level(logging.INFO, logger="yulon.winacl"):
        winacl.secure_folder(tmp_path)
        winacl.secure_folder(tmp_path)

    assert [(folder, sddl) for folder, sddl, _ in windows.applied] == [
        (tmp_path, winacl.owner_only_sddl(USER))
    ]
    assert "this account only" in caplog.text


def test_a_folder_that_is_already_owner_only_is_not_touched(
    tmp_path: Path, windows: FakeWindows
) -> None:
    windows.dacls[tmp_path] = OWNER_ONLY_AS_WINDOWS_READS_IT
    winacl.secure_folder(tmp_path)
    assert windows.applied == []


def test_a_folder_that_is_not_there_is_not_asked_about(
    tmp_path: Path, windows: FakeWindows
) -> None:
    winacl.secure_folder(tmp_path / "never-made")
    assert windows.reads == [] and windows.applied == []


def test_a_refused_apply_is_a_warning_and_not_an_exception(
    tmp_path: Path, windows: FakeWindows, caplog: pytest.LogCaptureFixture
) -> None:
    windows.refuse_apply = PermissionError(5, "Access is denied")
    with caplog.at_level(logging.WARNING, logger="yulon.winacl"):
        winacl.secure_folder(tmp_path)
    assert str(tmp_path) in caplog.text
    assert "PermissionError" in caplog.text


def test_an_apply_windows_accepted_and_did_not_keep_is_said(
    tmp_path: Path, windows: FakeWindows, caplog: pytest.LogCaptureFixture
) -> None:
    """The read-back is what the INFO line claims, so a change that did not land is a warning."""
    windows.apply_does_not_stick = True
    with caplog.at_level(logging.INFO, logger="yulon.winacl"):
        winacl.secure_folder(tmp_path)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "still" in warnings[0].getMessage()


def test_a_call_that_is_not_an_oserror_is_a_warning_too(
    tmp_path: Path, windows: FakeWindows, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ctypes.ArgumentError` is not an `OSError`, and a wrong signature must not cost the write."""

    def broken(folder: Path) -> str:
        raise TypeError("argument 1: wrong type")

    monkeypatch.setattr(winacl, "_read_dacl", broken)
    winacl.secure_folder(tmp_path)
    assert windows.applied == []


def test_off_windows_nothing_is_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX keeps the file mode, which is real there; no Win32 call is reached."""

    # Recorded rather than raised: `secure_folder` turns an exception into a
    # warning, so a raising stand-in would pass this test with the guard gone.
    reached: list[tuple[object, ...]] = []

    def must_not_run(*args: object) -> str:
        reached.append(args)
        return ""

    monkeypatch.setattr(winacl, "_on_windows", lambda: False)
    for name in ("_user_sid", "_read_dacl", "_apply_dacl"):
        monkeypatch.setattr(winacl, name, must_not_run)

    winacl.secure_folder(tmp_path)
    path = _save_credential(tmp_path)
    assert channel_setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path) is not None
    dbsecret.remember("wow-tbc", "ab12cd34", password=PASSWORD, volume="v", config_dir=tmp_path)
    assert dbsecret.recall("wow-tbc", "ab12cd34", config_dir=tmp_path) is not None
    assert path.exists()
    assert reached == []


# -- on the real write paths --------------------------------------------------


def test_the_credentials_folder_is_owner_only_before_the_credential_lands_in_it(
    tmp_path: Path, windows: FakeWindows
) -> None:
    path = _save_credential(tmp_path)

    assert windows.applied, "the credential was written without its folder being narrowed"
    folder, sddl, present = windows.applied[0]
    assert folder == path.parent == tmp_path / "credentials"
    assert sddl == winacl.owner_only_sddl(USER)
    assert present == [], f"{present} was already in the folder when its DACL changed"
    assert path.exists()


def test_the_pending_record_s_folder_is_owner_only_before_the_record_lands_in_it(
    tmp_path: Path, windows: FakeWindows
) -> None:
    pending = channel_setup.Idle().created("YULON_AB12CD34", PASSWORD)
    path = channel_setup.save_pending(
        pending, game="wow-wotlk", install_id="ab12cd34", config_dir=tmp_path
    )

    assert [(folder, present) for folder, _, present in windows.applied] == [(path.parent, [])]
    assert path.parent == tmp_path / "credentials" / "pending"


def test_a_refused_dacl_still_saves_the_credential(
    tmp_path: Path, windows: FakeWindows, caplog: pytest.LogCaptureFixture
) -> None:
    """The decision: warn and write. The password was already rotated on the server.

    Refusing the write would leave a GM account whose only copy of its password
    was in this process, so the next launch mints another and every round trip
    is a 401 -- a broken channel, to protect a file that is no worse than it
    was before T151.
    """
    windows.refuse_apply = PermissionError(5, "Access is denied")
    with caplog.at_level(logging.WARNING):
        path = _save_credential(tmp_path)

    endpoint = channel_setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path)
    assert endpoint is not None and endpoint.password == PASSWORD
    assert path.exists()
    assert "credentials" in caplog.text
    assert PASSWORD not in caplog.text


def test_reading_a_credential_written_before_t151_narrows_its_folder(
    tmp_path: Path, windows: FakeWindows, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upgrade: the file is there, its folder never had the DACL, and nothing rewrites it.

    A credential is written once and then only on a rotation, so without this
    the fix would reach an existing install's GM password never.
    """
    monkeypatch.setattr(winacl, "_on_windows", lambda: False)
    _save_credential(tmp_path)
    monkeypatch.setattr(winacl, "_on_windows", lambda: True)

    assert channel_setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path) is not None
    assert [folder for folder, _, _ in windows.applied] == [tmp_path / "credentials"]


def test_reading_where_nothing_was_ever_saved_asks_windows_nothing(
    tmp_path: Path, windows: FakeWindows
) -> None:
    assert channel_setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path) is None
    assert windows.reads == [] and windows.applied == []


def test_the_kept_database_password_s_folder_is_owner_only_before_it_lands(
    tmp_path: Path, windows: FakeWindows
) -> None:
    path = dbsecret.remember(
        "wow-tbc", "ab12cd34", password=PASSWORD, volume="v_db-data", config_dir=tmp_path
    )

    assert [(folder, present) for folder, _, present in windows.applied] == [(path.parent, [])]
    assert path.parent == tmp_path / dbsecret.DIR_NAME


def test_a_refused_dacl_still_keeps_the_database_password(
    tmp_path: Path, windows: FakeWindows
) -> None:
    """The one caller is an uninstall that deletes the only other copy next."""
    windows.refuse_apply = PermissionError(5, "Access is denied")
    dbsecret.remember("wow-tbc", "ab12cd34", password=PASSWORD, volume="v", config_dir=tmp_path)
    assert dbsecret.recall("wow-tbc", "ab12cd34", config_dir=tmp_path) == dbsecret.Kept(
        PASSWORD, "v"
    )


def test_reading_a_kept_password_written_before_t151_narrows_its_folder(
    tmp_path: Path, windows: FakeWindows, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(winacl, "_on_windows", lambda: False)
    dbsecret.remember("wow-tbc", "ab12cd34", password=PASSWORD, volume="v", config_dir=tmp_path)
    monkeypatch.setattr(winacl, "_on_windows", lambda: True)

    assert dbsecret.recall("wow-tbc", "ab12cd34", config_dir=tmp_path) is not None
    assert [folder for folder, _, _ in windows.applied] == [tmp_path / dbsecret.DIR_NAME]
