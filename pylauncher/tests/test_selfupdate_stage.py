"""Unpacking the new build inside the install, and proving it opens (T90 plan 3).

Real archives, built here with `tarfile` and `zipfile`, into `tmp_path`. No real
subprocess: the smoke test's runner is a seam, and what is asserted about it is
the argv, the environment and the timeout it is handed.

Nothing here writes outside `tmp_path`, and every refusal is checked for what it
LEFT as well as for what it said — the whole package's contract is that a
refusal removes what it staged and touches nothing of the player's.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from yulon.selfupdate import layout
from yulon.selfupdate import stage as stage_module
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import UpdateError
from yulon.selfupdate.stage import (
    entries_to_swap,
    prepare,
    sibling,
    smoke_test,
    stage,
    staged_executable,
)

PID = 4242


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


def _install(root: Path, kind: InstallKind = InstallKind.TARBALL, exe: str = "yulon") -> Install:
    """A folder install with the two entries a real build ships, plus a file of the player's."""
    target = root / "app"
    (target / "_internal").mkdir(parents=True)
    (target / "_internal" / "lib").write_text("the running build", encoding="utf-8")
    (target / exe).write_text("the running build", encoding="utf-8")
    return Install(kind, target, exe, True)


def _appimage(root: Path) -> Install:
    target = root / "Yulon-v0.8.66-Public-x86_64.AppImage"
    target.write_bytes(b"the running build")
    return Install(InstallKind.APPIMAGE, target, "", True)


def _tree(root: Path, *, ignoring: set[str] = frozenset()) -> str:  # type: ignore[assignment]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.relative_to(root).parts[0] in ignoring:
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _prepared(install: Install) -> Path:
    return prepare(install, "v0.8.70-Public", pid=PID)


WORK = set(layout.WORK_NAMES)
"""This app's own working directories, which "the install is untouched" is not about."""


# -- prepare -----------------------------------------------------------------


def test_preparing_makes_a_marked_staging_folder_inside_the_install(tmp_path: Path) -> None:
    install = _install(tmp_path)
    assert install.target is not None
    staged = _prepared(install)
    assert staged == install.target / layout.NEW_NAME
    marker = layout.read_marker(install, layout.NEW_NAME)
    assert marker is not None
    assert (marker.to_version, marker.pid) == ("v0.8.70-Public", PID)


def test_preparing_replaces_a_staging_folder_this_app_made(tmp_path: Path) -> None:
    install = _install(tmp_path)
    staged = _prepared(install)
    (staged / "left-over").write_text("x", encoding="utf-8")
    assert not (_prepared(install) / "left-over").exists()


def test_preparing_refuses_a_staging_folder_this_app_did_not_make(tmp_path: Path) -> None:
    """Somebody's own `.yulon-new` is somebody's folder. Refuse; never delete."""
    install = _install(tmp_path)
    assert install.target is not None
    theirs = install.target / layout.NEW_NAME
    theirs.mkdir()
    (theirs / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(UpdateError, match="cannot vouch for"):
        _prepared(install)
    assert (theirs / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_preparing_refuses_while_another_copy_is_updating(tmp_path: Path) -> None:
    import sys

    install = _install(tmp_path)
    alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        layout.write_marker(
            install,
            layout.NEW_NAME,
            layout.Marker(layout.NEW_NAME, "v1", "v2", alive.pid, layout.now()),
        )
        with pytest.raises(UpdateError, match="Another copy of Yu'lon"):
            _prepared(install)
    finally:
        alive.kill()
        alive.wait(timeout=30)
    assert alive.poll() is not None


# -- the tarball -------------------------------------------------------------


def test_a_tarball_stages_the_builds_entries_at_the_top_of_the_staging_folder(
    tmp_path: Path,
) -> None:
    """The archive's `yulon/` contents become `.yulon-new/*`; there is no extra nesting."""
    install = _install(tmp_path)
    _prepared(install)
    staged = stage(install, _good_tarball(tmp_path / "a.tar.gz"))

    assert staged == layout.work_dir(install, layout.NEW_NAME)
    assert (staged / "yulon").read_text(encoding="utf-8") == "new build\n"
    assert not (staged / "yulon").is_dir()
    assert (staged / "_internal" / "base_library.zip").exists()
    assert os.access(staged / "yulon", os.X_OK), "the executable bit did not survive"
    assert not (staged / "unpack").exists(), "the scratch directory was left behind"


@pytest.mark.parametrize(
    ("label", "members"),
    [
        ("escape", [("../evil", None, 0o644)]),
        ("outside-link", [("yulon/", "dir", 0o755), ("yulon/bad", "link", 0o777)]),
    ],
)
def test_an_archive_member_that_reaches_outside_is_refused_and_leaves_the_install_alone(
    label: str, members: list[tuple[str, str | None, int]], tmp_path: Path
) -> None:
    """`tarfile`'s own `data` filter decides this, not a rule re-implemented here."""
    install = _install(tmp_path)
    assert install.target is not None
    before = _tree(install.target, ignoring=WORK)
    _prepared(install)
    with pytest.raises(UpdateError):
        stage(install, _tarball(tmp_path / f"{label}.tar.gz", members))
    assert _tree(install.target, ignoring=WORK) == before, "the running install was touched"
    assert not (tmp_path / "evil").exists()


def test_an_absolute_member_cannot_escape_and_is_still_not_a_yulon_package(
    tmp_path: Path,
) -> None:
    """**The plan said this raises. It does not**, and the real behaviour is better.

    Measured on 3.13.15: `tarfile`'s `data` filter does not refuse `/etc/x` — it
    STRIPS the leading slash and extracts it to `etc/x` INSIDE the destination.
    So nothing escapes, and what refuses the archive is this module's own rule
    that a Yu'lon tarball's top level is exactly one `yulon/` directory.
    """
    install = _install(tmp_path)
    assert install.target is not None
    before = _tree(install.target, ignoring=WORK)
    _prepared(install)
    with pytest.raises(UpdateError, match="not a Yu'lon package"):
        stage(install, _tarball(tmp_path / "abs.tar.gz", [("/etc/x", None, 0o644)]))
    assert _tree(install.target, ignoring=WORK) == before
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
    install = _install(tmp_path)
    _prepared(install)
    with pytest.raises(UpdateError, match="not a Yu'lon package"):
        stage(install, _tarball(tmp_path / "odd.tar.gz", members))


def test_a_staged_tree_without_the_expected_executable_is_refused(tmp_path: Path) -> None:
    install = _install(tmp_path)
    _prepared(install)
    archive = _tarball(
        tmp_path / "noexe.tar.gz",
        [("yulon/", "dir", 0o755), ("yulon/something-else", None, 0o755)],
    )
    with pytest.raises(UpdateError, match="yulon"):
        stage(install, archive)


def test_an_archive_that_is_not_an_archive_is_a_sentence_not_a_traceback(tmp_path: Path) -> None:
    install = _install(tmp_path)
    _prepared(install)
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(b"this is not a gzip stream")
    with pytest.raises(UpdateError, match="could not be unpacked"):
        stage(install, broken)


# -- the size bound ----------------------------------------------------------


def test_an_archive_that_unpacks_to_more_than_the_cap_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 300 KB archive can unpack to gigabytes; the DOWNLOAD's size says nothing.

    The cap is lowered here rather than building a real gigabyte: what is under
    test is that the count is kept AS THE BYTES LAND, which a small cap proves
    exactly as well and in a tenth of a second.
    """
    # The fixture writes two 10-byte files, so 15 is crossed by the second.
    monkeypatch.setattr(stage_module, "MAX_UNPACKED_BYTES", 15)
    install = _install(tmp_path)
    assert install.target is not None
    before = _tree(install.target, ignoring=WORK)
    _prepared(install)
    with pytest.raises(UpdateError, match="unpacks to more than"):
        stage(install, _good_tarball(tmp_path / "big.tar.gz"))
    assert _tree(install.target, ignoring=WORK) == before


def test_an_archive_with_more_members_than_the_cap_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A million empty files cost no bytes and still take a filesystem apart."""
    monkeypatch.setattr(stage_module, "MAX_MEMBERS", 2)
    install = _install(tmp_path)
    _prepared(install)
    with pytest.raises(UpdateError, match="more than 2 files"):
        stage(install, _good_tarball(tmp_path / "many.tar.gz"))


def test_the_caps_are_set_above_the_real_artifacts(tmp_path: Path) -> None:
    """Measured on the published `v0.8.712-fixtest`: 231.6 MB / 652, and 151.8 MB / 391."""
    assert stage_module.MAX_UNPACKED_BYTES > 4 * 231_600_000
    assert stage_module.MAX_MEMBERS > 10 * 652


def test_a_zip_is_bounded_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stage_module, "MAX_UNPACKED_BYTES", 5)
    install = _install(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    _prepared(install)
    with pytest.raises(UpdateError, match="unpacks to more than"):
        stage(install, _zip(tmp_path / "a.zip", ["yulon.exe", "_internal/lib"]))


# -- the Windows zip ---------------------------------------------------------


def test_a_zip_with_no_top_level_directory_stages_its_members_directly(tmp_path: Path) -> None:
    install = _install(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    _prepared(install)
    staged = stage(install, _zip(tmp_path / "a.zip", ["yulon.exe", "_internal/base_library.zip"]))
    assert (staged / "yulon.exe").read_text(encoding="utf-8") == "new build\n"
    assert (staged / "_internal" / "base_library.zip").exists()


@pytest.mark.parametrize("name", ["..\\evil.exe", "../evil.exe", "C:/x.exe", "/abs.exe"])
def test_a_zip_member_that_names_somewhere_else_is_refused_and_leaves_nothing(
    name: str, tmp_path: Path
) -> None:
    """`zipfile` has no `data` filter, so the names are judged here — as BOTH kinds of path."""
    install = _install(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    assert install.target is not None
    before = _tree(install.target, ignoring=WORK)
    _prepared(install)
    with pytest.raises(UpdateError, match="somewhere else"):
        stage(install, _zip(tmp_path / "bad.zip", ["yulon.exe", name]))
    assert _tree(install.target, ignoring=WORK) == before
    assert not Path("/abs.exe").exists()


def test_a_zip_without_the_expected_executable_is_refused(tmp_path: Path) -> None:
    install = _install(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    _prepared(install)
    with pytest.raises(UpdateError, match="yulon.exe"):
        stage(install, _zip(tmp_path / "a.zip", ["something-else.exe"]))


# -- the AppImage ------------------------------------------------------------


def test_an_appimage_is_staged_under_a_fixed_name_inside_its_marked_folder(
    tmp_path: Path,
) -> None:
    install = _appimage(tmp_path)
    assert install.target is not None
    staged = _prepared(install)
    downloaded = staged / "Yulon-v0.8.70-Public-x86_64.AppImage"
    downloaded.write_bytes(b"the new build")

    out = stage(install, downloaded)

    assert out == layout.work_dir(install, layout.NEW_NAME)
    entry = out / layout.APPIMAGE_ENTRY
    assert entry.read_bytes() == b"the new build"
    assert os.access(entry, os.X_OK)
    assert install.target.read_bytes() == b"the running build", "the running AppImage was touched"
    assert staged_executable(install, out) == entry


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


def test_sibling_appends_to_the_name_and_never_descends_into_it(tmp_path: Path) -> None:
    assert sibling(tmp_path / "yulon", ".yulon-new") == tmp_path / "yulon.yulon-new"


# -- what may be replaced ----------------------------------------------------


def test_the_entries_are_the_staged_builds_own_with_the_executable_first(tmp_path: Path) -> None:
    """Executable first, so it leaves first and arrives last (`swap.py`'s contract)."""
    install = _install(tmp_path)
    _prepared(install)
    staged = stage(install, _good_tarball(tmp_path / "a.tar.gz"))
    assert entries_to_swap(install, staged) == ("yulon", "_internal")


def test_a_staged_entry_that_would_replace_a_file_of_the_players_refuses_the_update(
    tmp_path: Path,
) -> None:
    """**The rule the whole entry-level design exists for.**

    The running build's manifest says what it shipped. A staged `README.txt`
    landing on a `README.txt` the player wrote is not an update, it is a
    deletion — so the whole update is refused and the dialog falls back to
    downloading the file.
    """
    install = _install(tmp_path)
    assert install.target is not None
    (install.target / "README.txt").write_text("the player's notes", encoding="utf-8")
    _prepared(install)
    stage(
        install,
        _tarball(
            tmp_path / "extra.tar.gz",
            [
                ("yulon/", "dir", 0o755),
                ("yulon/yulon", None, 0o755),
                ("yulon/_internal/", "dir", 0o755),
                ("yulon/_internal/lib", None, 0o644),
                ("yulon/README.txt", None, 0o644),
            ],
        ),
    )
    with pytest.raises(UpdateError, match="did not put that there"):
        entries_to_swap(install, layout.work_dir(install, layout.NEW_NAME))
    assert (install.target / "README.txt").read_text(encoding="utf-8") == "the player's notes"


def test_a_staged_entry_the_manifest_does_name_is_replaceable(tmp_path: Path) -> None:
    """The other direction: a build that really does ship a third thing still updates."""
    install = _install(tmp_path)
    assert install.target is not None
    (install.target / "_internal" / layout.SHIPPED_MANIFEST).write_text(
        "yulon\n_internal\nREADME.txt\n", encoding="utf-8"
    )
    (install.target / "README.txt").write_text("shipped by the build", encoding="utf-8")
    _prepared(install)
    stage(
        install,
        _tarball(
            tmp_path / "extra.tar.gz",
            [
                ("yulon/", "dir", 0o755),
                ("yulon/yulon", None, 0o755),
                ("yulon/_internal/", "dir", 0o755),
                ("yulon/_internal/lib", None, 0o644),
                ("yulon/README.txt", None, 0o644),
            ],
        ),
    )
    entries = entries_to_swap(install, layout.work_dir(install, layout.NEW_NAME))
    assert entries[0] == "yulon"
    assert set(entries) == {"yulon", "_internal", "README.txt"}


def test_a_new_entry_that_collides_with_nothing_is_fine(tmp_path: Path) -> None:
    """A build that GAINS an entry: nothing of the player's is in its way."""
    install = _install(tmp_path)
    _prepared(install)
    stage(
        install,
        _tarball(
            tmp_path / "extra.tar.gz",
            [
                ("yulon/", "dir", 0o755),
                ("yulon/yulon", None, 0o755),
                ("yulon/_internal/", "dir", 0o755),
                ("yulon/_internal/lib", None, 0o644),
                ("yulon/newthing", None, 0o644),
            ],
        ),
    )
    entries = entries_to_swap(install, layout.work_dir(install, layout.NEW_NAME))
    assert "newthing" in entries and entries[0] == "yulon"


# -- the smoke test ----------------------------------------------------------


def test_a_staged_build_that_exits_zero_is_silent_and_was_run_the_right_way(
    tmp_path: Path,
) -> None:
    exe = tmp_path / "staged" / "yulon"
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
    """A frozen parent's `LD_LIBRARY_PATH` must not travel to the staged build."""
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
