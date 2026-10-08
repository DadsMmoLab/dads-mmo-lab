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


def test_an_appimage_env_pointing_at_nothing_is_not_an_appimage() -> None:
    """An inherited `$APPIMAGE` from some other app is not this app's install."""
    assert _detect(environ={"APPIMAGE": "/gone.AppImage"}).kind is InstallKind.TARBALL


_MOUNT = "/tmp/.mount_YulonAbC123"
_FOREIGN_MOUNT = "/tmp/.mount_OtherXyZ789"


def test_appimage_by_its_env(tmp_path: Path) -> None:
    """`$APPIMAGE` names the file on disk; `sys.executable` is inside the mount."""
    f = tmp_path / "Yulon-v0.8.66-Public-x86_64.AppImage"
    f.write_bytes(b"x")
    install = _detect(
        environ={"APPIMAGE": str(f), "APPDIR": _MOUNT},
        executable=Path(f"{_MOUNT}/usr/bin/yulon"),
    )
    assert (install.kind, install.target, install.executable) == (InstallKind.APPIMAGE, f, "")


def test_an_appimage_inherited_from_another_app_is_not_this_install(tmp_path: Path) -> None:
    """T578: an AppImage terminal or file manager exports APPIMAGE and APPDIR to its children.

    A tarball started from inside one is not that app, and an update that
    replaced the file named here would replace somebody else's program.
    """
    other = tmp_path / "OtherApp.AppImage"
    other.write_bytes(b"x")
    install = _detect(environ={"APPIMAGE": str(other), "APPDIR": _FOREIGN_MOUNT})
    assert (install.kind, install.target, install.executable) == (
        InstallKind.TARBALL,
        Path("/opt/apps/yulon"),
        "yulon",
    )


def test_an_appimage_variable_with_no_appdir_beside_it_is_not_believed(tmp_path: Path) -> None:
    """The runtime always sets both; a lone `$APPIMAGE` cannot be shown to be ours."""
    other = tmp_path / "OtherApp.AppImage"
    other.write_bytes(b"x")
    assert _detect(environ={"APPIMAGE": str(other)}).kind is InstallKind.TARBALL


def test_the_appdir_must_hold_the_running_binary_not_merely_be_a_prefix(tmp_path: Path) -> None:
    f = tmp_path / "Yulon.AppImage"
    f.write_bytes(b"x")
    install = _detect(
        environ={"APPIMAGE": str(f), "APPDIR": "/opt/apps/yul"},
        executable=Path("/opt/apps/yulon/yulon"),
    )
    assert install.kind is InstallKind.TARBALL


def test_running_from_a_mount_with_no_verified_appimage_is_its_own_kind(tmp_path: Path) -> None:
    """Env stripped (`env -i`, a launcher that cleans it): the file cannot be found.

    This is not a tarball: the binary is inside a mount that vanishes on exit,
    so there is no folder to replace and nothing to point an entry at.
    """
    mounted = Path(f"{_MOUNT}/usr/bin/yulon")
    for environ in ({}, {"APPIMAGE": "/gone.AppImage", "APPDIR": _MOUNT}):
        install = _detect(environ=environ, executable=mounted)
        assert install.kind is InstallKind.APPIMAGE_LOST
        assert install.target is None
        assert not install.can_swap
        assert install.artifact_name("v1.2.3-Public") is None


def test_a_foreign_appimage_in_the_environment_of_a_mounted_binary_is_not_adopted(
    tmp_path: Path,
) -> None:
    other = tmp_path / "OtherApp.AppImage"
    other.write_bytes(b"x")
    install = _detect(
        environ={"APPIMAGE": str(other), "APPDIR": _FOREIGN_MOUNT},
        executable=Path(f"{_MOUNT}/usr/bin/yulon"),
    )
    assert install.kind is InstallKind.APPIMAGE_LOST
    assert install.target is None


def test_appimage_file_answers_for_a_binary_inside_its_appdir_only(tmp_path: Path) -> None:
    f = tmp_path / "Yulon.AppImage"
    f.write_bytes(b"x")
    env = {"APPIMAGE": str(f), "APPDIR": _MOUNT}
    assert detect.appimage_file(env, executable=f"{_MOUNT}/usr/bin/yulon") == f
    assert detect.appimage_file(env, executable="/opt/yulon/yulon") is None
    assert detect.appimage_file({"APPIMAGE": str(f)}, executable=f"{_MOUNT}/usr/bin/yulon") is None
    assert detect.appimage_file({**env, "APPDIR": "/"}, executable="/opt/yulon/yulon") is None


def test_a_broad_appdir_is_not_a_runtime_folder(tmp_path: Path) -> None:
    """Codex adversarial: containment alone would accept `APPDIR=/opt/apps` for a tarball in it."""
    f = tmp_path / "Other.AppImage"
    f.write_bytes(b"x")
    install = _detect(environ={"APPIMAGE": str(f), "APPDIR": "/opt/apps"})
    assert install.kind is InstallKind.TARBALL


def test_a_symlinked_tmp_still_finds_the_mount(tmp_path: Path) -> None:
    """`/tmp` that is a link to somewhere else: the env and the binary may spell it differently."""
    real = tmp_path / "real"
    (real / ".mount_YulonQ1" / "usr" / "bin").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    f = tmp_path / "Yulon.AppImage"
    f.write_bytes(b"x")
    exe = real / ".mount_YulonQ1" / "usr" / "bin" / "yulon"
    env = {"APPIMAGE": str(f), "APPDIR": str(link / ".mount_YulonQ1")}
    assert detect.appimage_file(env, executable=exe) == f
    assert detect.in_appimage_mount(link / ".mount_YulonQ1" / "usr" / "bin" / "yulon")


def test_extract_and_run_is_recognised_the_same_way(tmp_path: Path) -> None:
    f = tmp_path / "Yulon.AppImage"
    f.write_bytes(b"x")
    root = "/tmp/appimage_extracted_0a1b2c"
    env = {"APPIMAGE": str(f), "APPDIR": root}
    assert detect.appimage_file(env, executable=f"{root}/usr/bin/yulon") == f


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
            {
                "environ": {"APPIMAGE": __file__, "APPDIR": "/tmp/.mount_x"},
                "executable": Path("/tmp/.mount_x/yulon"),
            },
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
        environ={"APPIMAGE": str(appimage), "APPDIR": "/tmp/.mount_x"},
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
