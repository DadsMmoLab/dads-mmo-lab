"""Unpacking the new build beside the old one, and proving it opens (T90 plan 3).

Real archives, built here with `tarfile` and `zipfile`, into `tmp_path`. No real
subprocess: the smoke test's runner is a seam, and what is asserted about it is
the argv, the environment and the timeout it is handed.

Nothing here writes outside `tmp_path`, and every refusal is checked for what it
LEFT as well as for what it said — the whole package's contract is that a
refusal removes what it staged.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import UpdateError
from yulon.selfupdate.stage import (
    discard,
    sibling,
    smoke_test,
    stage,
    staged_executable,
)


def _tarball(path: Path, members: list[tuple[str, str | None, int]]) -> Path:
    """A `.tar.gz` of `(name, kind, mode)` triples: kind None = file, `dir`, `link`."""
    with tarfile.open(path, "w:gz") as archive:
        for name, kind, mode in members:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = "../../../etc/passwd"
                archive.addfile(info)
            else:
                payload = b"new build\n"
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return path


def _good_tarball(path: Path) -> Path:
    return _tarball(
        path,
        [
            ("yulon/", "dir", 0o755),
            ("yulon/yulon", None, 0o755),
            ("yulon/_internal/", "dir", 0o755),
            ("yulon/_internal/base_library.zip", None, 0o644),
        ],
    )


def _zip(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "new build\n")
    return path


def _tarball_install(root: Path) -> Install:
    """A folder install at `<root>/yulon`, with a marker inside it to hash."""
    target = root / "yulon"
    target.mkdir()
    (target / "yulon").write_text("the running build", encoding="utf-8")
    return Install(InstallKind.TARBALL, target, "yulon", True)


def _windows_install(root: Path) -> Install:
    target = root / "Yulon"
    target.mkdir()
    (target / "yulon.exe").write_text("the running build", encoding="utf-8")
    return Install(InstallKind.WINDOWS_ZIP, target, "yulon.exe", True)


def _appimage_install(root: Path) -> Install:
    target = root / "Yulon-v0.8.66-Public-x86_64.AppImage"
    target.write_bytes(b"the running build")
    return Install(InstallKind.APPIMAGE, target, "", True)


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, by relative path, so "untouched" can be asserted."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


# -- sibling -----------------------------------------------------------------


def test_sibling_appends_to_the_name_and_never_descends_into_it(tmp_path: Path) -> None:
    """`.new` BESIDE the target: a folder target gets `yulon.new`, never `yulon/.new`."""
    assert sibling(tmp_path / "yulon", ".new") == tmp_path / "yulon.new"
    assert sibling(tmp_path / "a.AppImage", ".old") == tmp_path / "a.AppImage.old"


# -- the tarball -------------------------------------------------------------


def test_a_tarball_stages_its_top_level_directory_as_the_new_tree(tmp_path: Path) -> None:
    """The archive's `yulon/` BECOMES `<target>.new`; there is no `<target>.new/yulon`."""
    install = _tarball_install(tmp_path)
    staged = stage(install, _good_tarball(tmp_path / "a.tar.gz"))

    assert staged == tmp_path / "yulon.new"
    assert (staged / "yulon").read_text(encoding="utf-8") == "new build\n"
    assert not (staged / "yulon").is_dir(), "the top-level dir was nested instead of becoming .new"
    assert (staged / "_internal" / "base_library.zip").exists()
    assert os.access(staged / "yulon", os.X_OK), "the executable bit did not survive"
    assert not sibling(install.target or tmp_path, ".new-unpack").exists()


def test_a_stale_new_tree_is_replaced_and_never_merged(tmp_path: Path) -> None:
    install = _tarball_install(tmp_path)
    stale = tmp_path / "yulon.new"
    stale.mkdir()
    (stale / "left-over-from-last-time").write_text("x", encoding="utf-8")

    staged = stage(install, _good_tarball(tmp_path / "a.tar.gz"))
    assert not (staged / "left-over-from-last-time").exists()


@pytest.mark.parametrize(
    ("label", "members"),
    [
        ("escape", [("../evil", None, 0o644)]),
        ("outside-link", [("yulon/", "dir", 0o755), ("yulon/bad", "link", 0o777)]),
    ],
)
def test_an_archive_member_that_reaches_outside_is_refused_and_leaves_nothing(
    label: str, members: list[tuple[str, str | None, int]], tmp_path: Path
) -> None:
    """`tarfile`'s own `data` filter decides this, not a rule re-implemented here.

    Measured on 3.13.15 with a tarball of each shape: `../evil` raises
    `OutsideDestinationError` and `yulon/bad -> ../../../etc/passwd` raises
    `LinkOutsideDestinationError`, and the second leaves a half-made `yulon/`
    behind — which is why the refusal below has to discard as well as raise.
    """
    install = _tarball_install(tmp_path)
    before = _tree(tmp_path / "yulon")
    with pytest.raises(UpdateError):
        stage(install, _tarball(tmp_path / f"{label}.tar.gz", members))
    assert _tree(tmp_path / "yulon") == before, "the running install was touched"
    assert not (tmp_path / "yulon.new").exists()
    assert not (tmp_path / "yulon.new-unpack").exists()
    assert not (tmp_path / "evil").exists()


def test_an_absolute_member_cannot_escape_and_is_still_not_a_yulon_package(
    tmp_path: Path,
) -> None:
    """**The plan said this raises. It does not**, and the real behaviour is better.

    Measured on 3.13.15: `tarfile`'s `data` filter does not refuse `/etc/x` — it
    STRIPS the leading slash and extracts it to `etc/x` INSIDE the destination.
    So nothing escapes, and what refuses the archive is this module's own rule
    that a Yu'lon tarball's top level is exactly one `yulon/` directory. Both
    halves are asserted, because "it was refused" alone would not distinguish
    the two readings.
    """
    install = _tarball_install(tmp_path)
    before = _tree(tmp_path / "yulon")
    with pytest.raises(UpdateError, match="not a Yu'lon package"):
        stage(install, _tarball(tmp_path / "abs.tar.gz", [("/etc/x", None, 0o644)]))
    assert _tree(tmp_path / "yulon") == before
    assert not (tmp_path / "yulon.new").exists()
    assert not (tmp_path / "yulon.new-unpack").exists()
    assert not Path("/etc/x").exists(), "something really did write to /etc"


@pytest.mark.parametrize(
    "members",
    [
        [("notyulon/", "dir", 0o755), ("notyulon/yulon", None, 0o755)],
        [("yulon/", "dir", 0o755), ("extra/", "dir", 0o755)],
        [("loose", None, 0o644)],
        [],
    ],
)
def test_a_tarball_whose_top_level_is_not_one_yulon_directory_is_refused(
    members: list[tuple[str, str | None, int]], tmp_path: Path
) -> None:
    install = _tarball_install(tmp_path)
    with pytest.raises(UpdateError, match="not a Yu'lon package"):
        stage(install, _tarball(tmp_path / "odd.tar.gz", members))
    assert not (tmp_path / "yulon.new").exists()
    assert not (tmp_path / "yulon.new-unpack").exists()


def test_a_staged_tree_without_the_expected_executable_is_refused(tmp_path: Path) -> None:
    install = _tarball_install(tmp_path)
    archive = _tarball(
        tmp_path / "noexe.tar.gz",
        [("yulon/", "dir", 0o755), ("yulon/something-else", None, 0o755)],
    )
    with pytest.raises(UpdateError, match="yulon"):
        stage(install, archive)
    assert not (tmp_path / "yulon.new").exists()


def test_an_archive_that_is_not_an_archive_is_a_sentence_not_a_traceback(tmp_path: Path) -> None:
    install = _tarball_install(tmp_path)
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(b"this is not a gzip stream")
    with pytest.raises(UpdateError, match="could not be unpacked"):
        stage(install, broken)
    assert not (tmp_path / "yulon.new").exists()


# -- the Windows zip ---------------------------------------------------------


def test_a_zip_with_no_top_level_directory_stages_its_members_directly(tmp_path: Path) -> None:
    install = _windows_install(tmp_path)
    staged = stage(install, _zip(tmp_path / "a.zip", ["yulon.exe", "_internal/base_library.zip"]))
    assert staged == tmp_path / "Yulon.new"
    assert (staged / "yulon.exe").read_text(encoding="utf-8") == "new build\n"
    assert (staged / "_internal" / "base_library.zip").exists()


@pytest.mark.parametrize("name", ["..\\evil.exe", "../evil.exe", "C:/x.exe", "/abs.exe"])
def test_a_zip_member_that_names_somewhere_else_is_refused_and_leaves_nothing(
    name: str, tmp_path: Path
) -> None:
    """`zipfile` has no `data` filter, so the names are judged here — as BOTH kinds of path.

    Measured on 3.13.15, which is why both readings are needed:
    `PureWindowsPath("..\\evil.exe").parts` is `('..', 'evil.exe')` and
    `PureWindowsPath("/abs.exe").is_absolute()` is **False** (no drive), while
    `PurePosixPath("/abs.exe").is_absolute()` is True; `C:/x.exe` shows up only
    as a Windows DRIVE. One reading alone lets one of these through.
    """
    install = _windows_install(tmp_path)
    before = _tree(tmp_path / "Yulon")
    with pytest.raises(UpdateError, match="somewhere else"):
        stage(install, _zip(tmp_path / "bad.zip", ["yulon.exe", name]))
    assert _tree(tmp_path / "Yulon") == before
    assert not (tmp_path / "Yulon.new").exists()
    assert not (tmp_path / "Yulon.new-unpack").exists()
    assert not Path("/abs.exe").exists()


def test_a_zip_without_the_expected_executable_is_refused(tmp_path: Path) -> None:
    install = _windows_install(tmp_path)
    with pytest.raises(UpdateError, match="yulon.exe"):
        stage(install, _zip(tmp_path / "a.zip", ["something-else.exe"]))
    assert not (tmp_path / "Yulon.new").exists()


def test_a_zip_that_is_not_a_zip_is_a_sentence(tmp_path: Path) -> None:
    install = _windows_install(tmp_path)
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK not really")
    with pytest.raises(UpdateError, match="could not be unpacked"):
        stage(install, broken)


# -- the AppImage ------------------------------------------------------------


def test_an_appimage_is_copied_beside_itself_and_made_executable(tmp_path: Path) -> None:
    install = _appimage_install(tmp_path)
    downloaded = tmp_path / "Yulon-v0.8.70-Public-x86_64.AppImage"
    downloaded.write_bytes(b"the new build")

    staged = stage(install, downloaded)
    assert install.target is not None
    assert staged == sibling(install.target, ".new")
    assert staged.read_bytes() == b"the new build"
    assert os.access(staged, os.X_OK)
    assert install.target.read_bytes() == b"the running build", "the running AppImage was touched"


def test_a_stale_new_appimage_is_replaced(tmp_path: Path) -> None:
    install = _appimage_install(tmp_path)
    assert install.target is not None
    sibling(install.target, ".new").write_bytes(b"rubbish from last time")
    downloaded = tmp_path / "downloaded.AppImage"
    downloaded.write_bytes(b"the new build")
    assert stage(install, downloaded).read_bytes() == b"the new build"


# -- what cannot be staged ---------------------------------------------------


@pytest.mark.parametrize(
    "install",
    [
        Install(InstallKind.SOURCE, None, "", False),
        Install(InstallKind.MACOS_APP, None, "", False),
        Install(InstallKind.UNSUPPORTED, None, "", False),
    ],
)
def test_a_kind_with_nothing_to_replace_refuses_to_stage(install: Install, tmp_path: Path) -> None:
    with pytest.raises(UpdateError):
        stage(install, tmp_path / "anything.tar.gz")


# -- staged_executable -------------------------------------------------------


def test_the_staged_executable_is_inside_a_folder_and_is_the_file_itself_for_an_appimage(
    tmp_path: Path,
) -> None:
    folder = Install(InstallKind.TARBALL, tmp_path / "yulon", "yulon", True)
    assert staged_executable(folder, tmp_path / "yulon.new") == tmp_path / "yulon.new" / "yulon"
    appimage = Install(InstallKind.APPIMAGE, tmp_path / "a.AppImage", "", True)
    assert staged_executable(appimage, tmp_path / "a.AppImage.new") == tmp_path / "a.AppImage.new"


# -- the smoke test ----------------------------------------------------------


def test_a_staged_build_that_exits_zero_is_silent_and_was_run_the_right_way(
    tmp_path: Path,
) -> None:
    exe = tmp_path / "yulon.new" / "yulon"
    exe.parent.mkdir()
    exe.write_text("x", encoding="utf-8")
    seen: list[tuple[list[str], dict[str, str] | None, float]] = []

    def run(argv: list[str], env: dict[str, str] | None, timeout: float) -> int:
        seen.append((argv, env, timeout))
        return 0

    smoke_test(exe, run=run)

    argv, env, timeout = seen[0]
    assert argv == [str(exe)], "the staged binary is run by itself, with no arguments"
    assert env is not None
    assert env["YULON_SMOKE_TEST"] == "1"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert timeout == 60.0


def test_a_staged_build_that_exits_non_zero_is_refused_by_its_code(tmp_path: Path) -> None:
    exe = tmp_path / "yulon"
    exe.write_text("x", encoding="utf-8")
    with pytest.raises(UpdateError, match="3"):
        smoke_test(exe, run=lambda _argv, _env, _timeout: 3)


def test_a_staged_build_that_never_opens_is_refused_by_its_timeout(tmp_path: Path) -> None:
    exe = tmp_path / "yulon"
    exe.write_text("x", encoding="utf-8")

    def hang(_argv: list[str], _env: dict[str, str] | None, timeout: float) -> int:
        raise subprocess.TimeoutExpired(cmd="yulon", timeout=timeout)

    with pytest.raises(UpdateError, match="60"):
        smoke_test(exe, run=hang)


def test_a_staged_build_that_cannot_be_started_at_all_is_refused(tmp_path: Path) -> None:
    exe = tmp_path / "yulon"
    exe.write_text("x", encoding="utf-8")

    def refuse(_argv: list[str], _env: dict[str, str] | None, _timeout: float) -> int:
        raise OSError("Exec format error")

    with pytest.raises(UpdateError, match="Exec format error"):
        smoke_test(exe, run=refuse)


def test_the_smoke_environment_is_the_one_child_env_hands_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen parent's `LD_LIBRARY_PATH` must not travel to the staged build.

    `runner.child_env()` is the app's one rule for that and this uses it rather
    than copying `os.environ`, so the two cannot drift. Driven through the REAL
    `child_env()` with `sys.frozen` set, because a test that only checked the
    two variables above would pass against a copy of `os.environ`.
    """
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/the/old/build/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/what/the/user/had")
    seen: list[dict[str, str] | None] = []

    exe = tmp_path / "yulon"
    exe.write_text("x", encoding="utf-8")
    smoke_test(exe, run=lambda _argv, env, _timeout: (seen.append(env), 0)[1])

    env = seen[0]
    assert env is not None
    assert env["LD_LIBRARY_PATH"] == "/what/the/user/had"
    assert "LD_LIBRARY_PATH_ORIG" not in env


# -- discard -----------------------------------------------------------------


def test_discard_removes_a_file_a_tree_and_nothing_it_was_not_given(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "deep").mkdir(parents=True)
    (tree / "deep" / "f").write_text("x", encoding="utf-8")
    loose = tmp_path / "loose"
    loose.write_text("x", encoding="utf-8")
    keep = tmp_path / "keep"
    keep.write_text("x", encoding="utf-8")

    discard(tree, loose, tmp_path / "never-existed")

    assert not tree.exists() and not loose.exists()
    assert keep.read_text(encoding="utf-8") == "x"


def test_discard_never_raises_even_on_something_it_cannot_remove(tmp_path: Path) -> None:
    """It runs on the failure path; a `discard` that raised would replace the real reason."""
    parent = tmp_path / "ro"
    parent.mkdir()
    victim = parent / "tree"
    victim.mkdir()
    (victim / "f").write_text("x", encoding="utf-8")
    parent.chmod(0o500)
    try:
        discard(victim)
        assert victim.exists(), "the precondition: this one really could not be removed"
    finally:
        parent.chmod(0o700)
