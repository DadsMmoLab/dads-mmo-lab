"""What kind of install is running, and what it may do about an update (T90 plan 3).

Everything here is by INJECTION. `detect_install()` takes `frozen`,
`platform_id`, `machine`, `environ`, `executable` and the writability probe as
keyword arguments precisely so that no test has to monkeypatch `sys.frozen` or
`sys.executable` — a module-level patch of either is a patch every other test in
the process can see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon.selfupdate import detect
from yulon.selfupdate.detect import (
    DOWNLOAD,
    OPEN_PAGE,
    UPDATE_NOW,
    Install,
    InstallKind,
    action_label,
    detect_install,
)
from yulon.update import ReleaseAsset, UpdateCheck

REPO_ROOT = Path(__file__).resolve().parents[2]


def _detect(**kw: object) -> Install:
    """A frozen x86_64 Linux install, with each fact overridable one at a time."""
    base: dict[str, object] = dict(
        frozen=True,
        platform_id="linux",
        machine="x86_64",
        environ={},
        executable=Path("/opt/apps/yulon/yulon"),
        probe_writable=lambda _p: True,
    )
    return detect_install(**{**base, **kw})  # type: ignore[arg-type]


# -- the arms ---------------------------------------------------------------


def test_a_checkout_is_source() -> None:
    assert _detect(frozen=False).kind is InstallKind.SOURCE


def test_appimage_by_its_env(tmp_path: Path) -> None:
    """`$APPIMAGE` names the file on disk; `sys.executable` is inside the mount."""
    f = tmp_path / "Yulon-v0.8.66-Public-x86_64.AppImage"
    f.write_bytes(b"x")
    install = _detect(environ={"APPIMAGE": str(f)}, executable=Path("/tmp/.mount_x/yulon"))
    assert (install.kind, install.target, install.executable) == (InstallKind.APPIMAGE, f, "")


def test_an_appimage_env_pointing_at_nothing_is_not_an_appimage() -> None:
    """An inherited `$APPIMAGE` from some other app is not this app's install."""
    assert _detect(environ={"APPIMAGE": "/gone.AppImage"}).kind is InstallKind.TARBALL


def test_tarball_target_is_the_folder_holding_the_binary() -> None:
    install = _detect()
    assert (install.kind, install.target, install.executable) == (
        InstallKind.TARBALL,
        Path("/opt/apps/yulon"),
        "yulon",
    )


def test_windows() -> None:
    install = _detect(platform_id="windows", machine="AMD64", executable=Path("C:/G/yulon.exe"))
    assert (install.kind, install.executable) == (InstallKind.WINDOWS_ZIP, "yulon.exe")
    assert install.target == Path("C:/G")


def test_macos_is_its_own_kind_and_never_swaps() -> None:
    install = _detect(
        platform_id="macos",
        machine="arm64",
        executable=Path("/Applications/Yulon.app/Contents/MacOS/yulon"),
    )
    assert install.kind is InstallKind.MACOS_APP
    assert not install.can_swap


@pytest.mark.parametrize(
    ("platform_id", "machine"),
    [("linux", "aarch64"), ("macos", "x86_64"), ("windows", "ARM64")],
)
def test_a_machine_nothing_is_built_for_is_unsupported(platform_id: str, machine: str) -> None:
    assert _detect(platform_id=platform_id, machine=machine).kind is InstallKind.UNSUPPORTED


def test_read_only_cannot_swap_but_still_names_its_artifact() -> None:
    """A folder this user cannot write is a Download, not a dead end."""
    install = _detect(probe_writable=lambda _p: False)
    assert install.kind is InstallKind.TARBALL, "the precondition: this is the swappable kind"
    assert not install.can_swap
    assert install.artifact_name("v0.8.70-Public") == "Yulon-v0.8.70-Public-x86_64.tar.gz"


def test_a_machine_with_no_artifact_names_none() -> None:
    assert _detect(frozen=False).artifact_name("v1.2.3-Public") is None
    assert _detect(machine="aarch64").artifact_name("v1.2.3-Public") is None


@pytest.mark.parametrize(
    ("kw", "name"),
    [
        ({}, "Yulon-v1.2.3-Public-x86_64.tar.gz"),
        (
            {"environ": {"APPIMAGE": __file__}, "executable": Path("/tmp/.mount_x/yulon")},
            "Yulon-v1.2.3-Public-x86_64.AppImage",
        ),
        ({"platform_id": "windows", "machine": "AMD64"}, "Yulon-v1.2.3-Public-windows-x64.zip"),
        ({"platform_id": "macos", "machine": "arm64"}, "Yulon-v1.2.3-Public-macos.dmg"),
    ],
)
def test_artifact_names_match_release_yml(kw: dict[str, object], name: str) -> None:
    assert _detect(**kw).artifact_name("v1.2.3-Public") == name


def test_the_names_really_are_the_ones_release_yml_writes() -> None:
    """The join, read off `_SUFFIX` itself rather than off a second copy of it.

    The app computes the name it downloads from the tag and never reads one off
    the feed, which means a suffix that drifts from `release.yml` is a release
    the app decides it has no artifact in — silently, and only on the platform
    whose line moved.

    **Enumerating the four fragments here instead would be the vacuous form of
    this test**, and it was written that way first: mutating `_SUFFIX`'s
    tarball entry to `-x86_64.tgz` left this green (measured), because nothing
    in it touched the module. It iterates `detect._SUFFIX` now, so the workflow
    is checked for the names the app will really ask for; the dict is also
    asserted to hold all four kinds, or a deleted row would simply not be
    checked.
    """
    text = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert set(detect._SUFFIX) == {
        InstallKind.APPIMAGE,
        InstallKind.TARBALL,
        InstallKind.WINDOWS_ZIP,
        InstallKind.MACOS_APP,
    }, "a kind lost its suffix, so the loop below would not check it"
    for kind, fragment in detect._SUFFIX.items():
        assert (
            f"Yulon-${{YULON_REF}}{fragment}" in text or f"Yulon-$env:YULON_REF{fragment}" in text
        ), f"{kind} asks for {fragment!r}, which release.yml does not build"


def test_x86_64_is_spelled_two_ways_and_both_are_the_same_machine() -> None:
    """`platform.machine()` says `x86_64` on Linux and `AMD64` on Windows."""
    assert _detect(machine="AMD64").kind is InstallKind.TARBALL
    assert _detect(platform_id="windows", machine="x86_64").kind is InstallKind.WINDOWS_ZIP


# -- the real probe ----------------------------------------------------------


def test_the_real_probe_says_yes_for_a_writable_folder_and_leaves_nothing(tmp_path: Path) -> None:
    target = tmp_path / "yulon"
    target.mkdir()
    assert detect._probe(target) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["yulon"]


def test_the_real_probe_says_no_when_the_parent_is_missing(tmp_path: Path) -> None:
    assert detect._probe(tmp_path / "a" / "b") is False


def test_the_real_probe_says_no_for_a_folder_nobody_may_write(tmp_path: Path) -> None:
    """Tried rather than asked: `os.access` lies on Windows ACLs and network mounts."""
    target = tmp_path / "yulon"
    target.mkdir()
    target.chmod(0o500)
    try:
        assert detect._probe(target) is False
    finally:
        target.chmod(0o700)


def test_a_folder_install_is_asked_about_itself_and_an_appimage_about_its_parent(
    tmp_path: Path,
) -> None:
    """**Which directory is probed changed in cold review 1**, with the design.

    The swap used to rename the whole install folder, so the question was "can
    I make a sibling of it". It now stages into `<target>/.yulon-new`, so the
    question is "can I make a file INSIDE it" — and getting that wrong would
    offer "Update now" on a folder the update cannot write to. An AppImage is
    still a file whose work directories are siblings, so that one still asks
    its parent.
    """
    asked: list[Path] = []
    _detect(probe_writable=lambda p: asked.append(p) or True)
    assert asked == [Path("/opt/apps/yulon")], "a folder install must be asked about itself"

    asked.clear()
    appimage = tmp_path / "Yulon.AppImage"
    appimage.write_bytes(b"x")
    _detect(
        environ={"APPIMAGE": str(appimage)},
        executable=Path("/tmp/.mount_x/yulon"),
        probe_writable=lambda p: asked.append(p) or True,
    )
    assert asked == [tmp_path], "an AppImage stages beside itself, so its parent is the question"


# -- the label ---------------------------------------------------------------

ARTIFACT = "Yulon-v0.8.70-Public-x86_64.tar.gz"


def _offer(*, checksums: bool = True, assets: tuple[str, ...] = (ARTIFACT,)) -> UpdateCheck:
    return UpdateCheck(
        current="0.8.66-Public",
        latest="v0.8.70-Public",
        available=True,
        url="https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v0.8.70-Public",
        assets=tuple(ReleaseAsset(n, f"https://example.invalid/{n}", 10) for n in assets),
        has_checksums=checksums,
    )


def test_a_swappable_install_with_checksums_and_its_artifact_updates_now() -> None:
    assert action_label(_detect(), _offer()) == UPDATE_NOW


def test_a_read_only_install_downloads() -> None:
    assert action_label(_detect(probe_writable=lambda _p: False), _offer()) == DOWNLOAD


def test_macos_downloads() -> None:
    mac = _detect(platform_id="macos", machine="arm64")
    offer = _offer(assets=("Yulon-v0.8.70-Public-macos.dmg",))
    assert action_label(mac, offer) == DOWNLOAD


def test_a_release_with_no_checksums_is_never_installed_only_opened() -> None:
    assert action_label(_detect(), _offer(checksums=False)) == OPEN_PAGE


def test_a_release_that_does_not_carry_this_machines_artifact_is_only_opened() -> None:
    offer = _offer(assets=("Yulon-v0.8.70-Public-windows-x64.zip", "SHA256SUMS"))
    assert offer.has_checksums, "the precondition: only the ARTIFACT is missing here"
    assert action_label(_detect(), offer) == OPEN_PAGE


def test_a_checkout_is_only_ever_offered_the_page() -> None:
    assert action_label(_detect(frozen=False), _offer()) == OPEN_PAGE


def test_an_unsupported_machine_is_only_ever_offered_the_page() -> None:
    assert action_label(_detect(machine="aarch64"), _offer()) == OPEN_PAGE


def test_an_offer_with_no_tag_is_only_ever_offered_the_page() -> None:
    """`latest` is `None` when the check found nothing; there is no name to ask for."""
    nothing = UpdateCheck("0.8.66-Public", None, False, "https://example.invalid/")
    assert action_label(_detect(), nothing) == OPEN_PAGE
