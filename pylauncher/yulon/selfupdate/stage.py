"""Unpack the new build beside the old one, and prove it opens.

Beside, because the swap is then a RENAME: `<target>.new` and `<target>` are
siblings on the same filesystem, so the helper that replaces one with the other
never copies bytes and never leaves a half-written install.

**The archive's own safety is the standard library's, not a rule re-written
here.** `tarfile`'s `data` filter is what refuses a member that would land
outside the destination or a link that points out of it, and this module
refuses to extract at all if the interpreter has no such filter. `zipfile` has
no equivalent, so its member names ARE judged here — and as both a POSIX and a
Windows path, because neither reading catches all four shapes on its own (the
test carries the measurement).

What this module's own rules add is small and specific: a Yu'lon tarball's top
level is exactly one `yulon/` directory, and a staged tree has to carry the
executable this install launches. Everything else is the stdlib's.

Every refusal discards what it staged before it raises, so the folder beside
the install is either a complete new build or nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath

from yulon import runner
from yulon.log import get_logger
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import UpdateError

logger = get_logger(__name__)

TOP_LEVEL_DIR = "yulon"
"""What `tar -czf … -C dist yulon` puts at the top of the Linux tarball."""

SMOKE_TIMEOUT_SECONDS = 60.0
"""How long the staged build has to open a window and leave. It takes seconds."""

_NOT_A_PACKAGE = (
    "That download is not a Yu'lon package (its top level is not a single "
    "`yulon` folder). Nothing was installed."
)


def sibling(target: Path, suffix: str) -> Path:
    """`<target><suffix>` — BESIDE `target`, never inside it.

    `Path(str(target) + suffix)` and not `target.with_suffix()`: a folder called
    `yulon` has no suffix to replace, and an AppImage called
    `Yulon-v0.8.70-Public-x86_64.AppImage` would lose `.AppImage`.
    """
    return Path(str(target) + suffix)


def discard(*paths: Path) -> None:
    """Remove each path, file or tree. **Never raises.**

    It runs on the failure path, and an exception raised here would replace the
    real reason the update was refused with a second, less useful one.
    """
    for path in paths:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.info(f"self-update: could not remove {path}: {exc}")


def _require_a_data_filter() -> None:
    """Refuse to extract on an interpreter whose `tarfile` has no `data` filter.

    Refusing rather than falling back to an unfiltered `extractall`: the whole
    reason this module does not implement its own path rules is that the
    stdlib's are better, and "the stdlib's rules, or nobody's" is not a trade
    to make silently. Added in 3.11.4; this app requires 3.11.
    """
    if not hasattr(tarfile, "data_filter"):  # pragma: no cover - 3.11.4+ everywhere we run
        raise UpdateError(
            "This Python cannot unpack an update safely (its tarfile has no data filter). "
            "Install the new version from the release page instead."
        )


def stage(install: Install, archive: Path) -> Path:
    """Put the downloaded build at `<target>.new` and return that path.

    A stale `<target>.new` from an earlier attempt is REPLACED, never merged:
    what is left of a refused update is not a head start, it is rubbish that
    would otherwise be launched.
    """
    target = install.target
    if target is None or install.kind not in (
        InstallKind.APPIMAGE,
        InstallKind.TARBALL,
        InstallKind.WINDOWS_ZIP,
    ):
        raise UpdateError("Yu'lon cannot replace this kind of install by itself.")
    staged = sibling(target, ".new")
    unpack = sibling(target, ".new-unpack")
    discard(staged, unpack)
    try:
        if install.kind is InstallKind.APPIMAGE:
            _stage_appimage(archive, staged)
        else:
            _stage_archive(install, archive, unpack, staged)
    except BaseException:
        discard(staged, unpack)
        raise
    return staged


def _stage_appimage(archive: Path, staged: Path) -> None:
    """An AppImage is one file: the download IS the new build, copied and made runnable."""
    try:
        shutil.copyfile(archive, staged)
        os.chmod(staged, 0o755)
    except OSError as exc:
        raise UpdateError(f"The new AppImage could not be put in place: {exc}") from exc


def _stage_archive(install: Install, archive: Path, unpack: Path, staged: Path) -> None:
    """Unpack into `<target>.new-unpack`, then rename the right directory to `<target>.new`.

    Two steps rather than one, and the reason is the tarball's shape: its top
    level is a `yulon/` directory that has to BECOME `<target>.new` rather than
    sit inside it, so the extraction needs somewhere to land first. The zip has
    no top-level directory, so its unpack dir is the new tree and the rename is
    the whole of the second step.
    """
    unpack.mkdir(parents=True)
    if install.kind is InstallKind.TARBALL:
        _extract_tar(archive, unpack)
        new_tree = _the_one_yulon_directory(unpack)
    else:
        _extract_zip(archive, unpack)
        new_tree = unpack
    executable = new_tree / install.executable
    if not executable.is_file():
        raise UpdateError(
            f"That download does not contain {install.executable}, so it is not a build of "
            "Yu'lon for this computer. Nothing was installed."
        )
    os.replace(new_tree, staged)
    discard(unpack)


def _extract_tar(archive: Path, into: Path) -> None:
    """`tarfile` with `filter="data"`, which is the whole of the path defence here.

    Measured on 3.13.15 against tarballs built for each shape: `../evil` raises
    `OutsideDestinationError`; a member linking to `../../../etc/passwd` raises
    `LinkOutsideDestinationError`; and `/etc/x` does **not** raise — the filter
    strips the leading slash and extracts it to `etc/x` inside the destination,
    where `_the_one_yulon_directory` then refuses the archive for not being a
    Yu'lon package. The executable bit survives and a setuid bit is stripped
    (`0o4755` came out `0o755`).
    """
    _require_a_data_filter()
    try:
        with tarfile.open(archive) as handle:
            handle.extractall(into, filter="data")
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise UpdateError(f"The download could not be unpacked: {exc}") from exc


def _names_somewhere_else(name: str) -> bool:
    """Would extracting this zip member land anywhere but under the destination?

    Read as BOTH a POSIX and a Windows path, because neither alone sees all
    four shapes (measured on 3.13.15):

        "..\\evil.exe"   PureWindowsPath parts ('..', 'evil.exe')   - Windows
        "../evil.exe"    PureWindowsPath parts ('..', 'evil.exe')   - either
        "C:/x.exe"       PureWindowsPath drive 'C:'                 - Windows
        "/abs.exe"       PurePosixPath is_absolute True             - POSIX
                         (PureWindowsPath.is_absolute is FALSE here: no drive)
    """
    windows = PureWindowsPath(name)
    return bool(
        PurePosixPath(name).is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in windows.parts
    )


def _extract_zip(archive: Path, into: Path) -> None:
    """Every member name judged first, then one `extractall`.

    Judged BEFORE anything is written, so a zip whose tenth member is
    `..\\evil.exe` does not leave the first nine on disk — and the caller's
    discard would have removed those anyway, but only from the unpack folder,
    which is precisely not where an escaping member goes.
    """
    try:
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                if _names_somewhere_else(info.filename):
                    raise UpdateError(
                        f"That download contains a file that names somewhere else on this "
                        f"computer ({info.filename!r}). Nothing was installed."
                    )
            handle.extractall(into)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise UpdateError(f"The download could not be unpacked: {exc}") from exc


def _the_one_yulon_directory(unpack: Path) -> Path:
    """The archive's single top-level `yulon/`, or a refusal.

    A listing that decides a refusal and never a write: what it can say is "no"
    (`_NOT_A_PACKAGE`) or "here is the folder to rename", and an unreadable
    folder is the same "no" — this is a directory this function created itself,
    moments earlier.
    """
    try:
        entries = sorted(unpack.iterdir())
    except OSError as exc:
        raise UpdateError(f"The download could not be unpacked: {exc}") from exc
    if len(entries) != 1 or entries[0].name != TOP_LEVEL_DIR or not entries[0].is_dir():
        logger.info(f"self-update: the archive's top level is {[e.name for e in entries]}")
        raise UpdateError(_NOT_A_PACKAGE)
    return entries[0]


def staged_executable(install: Install, staged: Path) -> Path:
    """What to run to prove the staged build opens: a file target IS the executable."""
    return staged / install.executable if install.executable else staged


RunSmoke = Callable[[list[str], "dict[str, str] | None", float], int]
"""`(argv, env, timeout) -> exit code`. Raises `TimeoutExpired` or `OSError` like `subprocess`."""


def _run(argv: list[str], env: dict[str, str] | None, timeout: float) -> int:
    """Start the staged build with every stream closed and no console window."""
    completed = subprocess.run(
        argv,
        env=env,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=runner.creationflags(),
    )
    return completed.returncode


def smoke_test(exe: Path, *, run: RunSmoke = _run, timeout: float = SMOKE_TIMEOUT_SECONDS) -> None:
    """Run the staged build once under `YULON_SMOKE_TEST` and require exit 0.

    `main()` answers that variable by building the whole window and returning 0
    without entering the event loop, so this proves the thing a packaging fault
    actually breaks: that the bundle's Qt, its plugins and its data files are
    all present on THIS machine. It is the last check before the running
    install is replaced.

    The environment comes from `runner.child_env()`, which is the app's one
    rule for not handing a child the frozen parent's `LD_LIBRARY_PATH`.

    `PYINSTALLER_RESET_ENVIRONMENT` is PyInstaller's documented way for a
    frozen process to tell another frozen process's bootloader to undo what the
    parent's bootloader did. **Measured on PyInstaller 6.22.3, Linux, with a
    real one-dir build (2026-09-21): it changed nothing observable here** — the
    child's `LD_LIBRARY_PATH` came out identical with and without it, because
    the child's own bootloader PREPENDS its own `_internal` and an inherited
    path only ever lands behind it. It is set anyway, and said plainly rather
    than claimed: the string is in the Linux bootloader binary, the Windows and
    macOS bootloaders could not be measured from here, and `child_env()` is
    what actually removes the leak.
    """
    env = runner.child_env() or dict(os.environ)
    env["YULON_SMOKE_TEST"] = "1"
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        code = run([str(exe)], env, timeout)
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            f"The new version did not open within {timeout:.0f} seconds, so Yu'lon has not "
            "installed it. Nothing was changed."
        ) from exc
    except OSError as exc:
        raise UpdateError(
            f"The new version could not be started ({exc}), so Yu'lon has not installed it. "
            "Nothing was changed."
        ) from exc
    if code != 0:
        raise UpdateError(
            f"The new version did not start properly (it stopped with exit code {code}), so "
            "Yu'lon has not installed it. Nothing was changed."
        )
