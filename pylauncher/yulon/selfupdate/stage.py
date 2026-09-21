"""Unpack the new build inside the install, and prove it opens.

**Inside, not beside** (cold review 1). The staged build lands in
`<target>/.yulon-new/`, which is on the same filesystem as the entries it will
replace by construction, and which cannot be confused with anything of the
player's: it is a marked working directory, and `layout.py` records why every
delete in this package is gated on that marker.

**The archive's own safety is the standard library's, not a rule re-written
here.** `tarfile`'s `data` filter refuses a member that would land outside the
destination or a link that points out of it, and this module refuses to
extract at all if the interpreter has no such filter. `zipfile` has no
equivalent, so its member names ARE judged here — as both a POSIX and a
Windows path, because neither reading catches all four shapes on its own.

What this module adds is three rules of its own:

* a Yu'lon tarball's top level is exactly one `yulon/` directory;
* the unpacked build is bounded in bytes and in member count, so a small
  archive cannot fill the disk;
* **a staged entry may replace an entry in the install folder only if the
  running build SHIPPED that name.** A collision with anything else — a
  `README.txt` the player put there — refuses the whole update, because moving
  a file this app cannot prove is its own is the defect the entry-level design
  was written to remove.

Every refusal discards what it staged before it raises, and discards it
through `layout.discard_ours`, so it can only ever remove a marked directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath

from yulon import __version__, runner
from yulon.log import get_logger
from yulon.selfupdate import layout
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import UpdateError

logger = get_logger(__name__)

TOP_LEVEL_DIR = "yulon"
"""What `tar -czf … -C dist yulon` puts at the top of the Linux tarball."""

SMOKE_TIMEOUT_SECONDS = 60.0
"""How long the staged build has to open a window and leave. It takes seconds."""

MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
"""How much an archive may unpack to before it is refused. **Measured, not chosen.**

The real published artifacts of `v0.8.712-fixtest`: the Linux tar.gz unpacks to
**231.6 MB** over 652 members, and the Windows zip to **151.8 MB** over 391.
One gigabyte is a little over four times the larger of the two, which leaves
room for a bundle that grows and still refuses the case this exists for — a
300 KB archive that unpacks to gigabytes, which the size of the DOWNLOAD says
nothing about.
"""

MAX_MEMBERS = 20_000
"""How many files an archive may hold. 652 and 391 in the real artifacts.

Counted as well as the bytes, because a million empty files cost no bytes and
still take a filesystem apart.
"""

_NOT_A_PACKAGE = (
    "That download is not a Yu'lon package (its top level is not a single "
    "`yulon` folder). Nothing was installed."
)


def sibling(target: Path, suffix: str) -> Path:
    """`<target><suffix>` — beside `target`, never inside it.

    Only the AppImage (a file install) uses this shape now; a folder install
    keeps everything under `<target>/.yulon-*`. Kept as one function because
    `layout.work_dir()` builds both from it and the two must agree.
    """
    return Path(str(target) + suffix)


def discard(*paths: Path) -> None:
    """Remove each path, file or tree. **Never raises.**

    **Private to this module's own unpack scratch.** Everything a caller can
    name goes through `layout.discard_ours`, which will only remove a marked
    working directory; this one exists for the directory `_extract` fills and
    then renames, which belongs to a single call and never outlives it.
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


def prepare(install: Install, version: str, *, pid: int) -> Path:
    """Make a fresh, marked `.yulon-new` and `.yulon-download`, and return the staging one.

    Refuses if either is already there and this app cannot prove it made it —
    that is somebody's own folder and it is not ours to delete — and refuses if
    another live copy of Yu'lon is staging into the same install.

    **Two directories, because the archive is not part of the build.** It used
    to be downloaded into the staging directory, which made it one of the
    entries `staged_entries()` reports and the helper then moved a 90 MB
    tarball into the player's folder as part of the program (cold review 2).
    """
    busy = layout.another_copy_is_updating(install, pid)
    if busy is not None:
        raise UpdateError(busy)
    for name in layout.WORK_NAMES:
        path = layout.work_dir(install, name)
        if path.exists() and not layout.discard_ours(install, name):
            raise UpdateError(
                f"There is a {path.name} folder beside Yu'lon that this copy cannot vouch "
                "for — it was left by an earlier update, or by another copy of Yu'lon, and "
                "Yu'lon will not delete a folder it cannot prove it made. Move or rename it, "
                "then try again."
            )
    for name in (layout.NEW_NAME, layout.DOWNLOAD_NAME):
        _make_work_dir(install, name, version, pid)
    return layout.work_dir(install, layout.NEW_NAME)


def _make_work_dir(install: Install, name: str, version: str, pid: int) -> None:
    """Create one marked working directory, or leave nothing behind.

    **The marker failing used to leave the directory** (cold review 2, S1): a
    disk that filled up between `mkdir` and the write left an empty
    `.yulon-new` that every later attempt refused, for ever. The directory is
    removed here if the marker cannot be written, and `layout` additionally
    counts an EMPTY work dir as this app's — two independent ways out of the
    same wedge.
    """
    path = layout.work_dir(install, name)
    try:
        path.mkdir(parents=True)
    except OSError as exc:
        raise UpdateError(f"Yu'lon could not prepare a place for the update: {exc}") from exc
    try:
        layout.write_marker(
            install,
            name,
            layout.Marker(
                role=name,
                from_version=__version__,
                to_version=version,
                pid=pid,
                stamp=layout.now(),
            ),
        )
    except OSError as exc:
        try:
            path.rmdir()
        except OSError:  # pragma: no cover - it was empty a moment ago
            logger.info(f"self-update: {path} was left behind after a failed marker")
        raise UpdateError(f"Yu'lon could not prepare a place for the update: {exc}") from exc


def stage(install: Install, archive: Path) -> Path:
    """Put the downloaded build at `.yulon-new` and return that path.

    `prepare()` has already made the marked directory; this fills it. A folder
    install unpacks into a scratch dir inside it and then moves the build's
    entries up; a file install (the AppImage) IS one file, so the download is
    copied to `<file>.yulon-new`.
    """
    target = install.target
    if target is None or install.kind not in (
        InstallKind.APPIMAGE,
        InstallKind.TARBALL,
        InstallKind.WINDOWS_ZIP,
    ):
        raise UpdateError("Yu'lon cannot replace this kind of install by itself.")
    staged = layout.work_dir(install, layout.NEW_NAME)
    if install.kind is InstallKind.APPIMAGE:
        _stage_appimage(archive, staged / layout.APPIMAGE_ENTRY)
        return staged
    # The scratch directory lives in the DOWNLOAD work dir, not in the staging
    # one: `.yulon-new` must end up holding exactly the build's own top-level
    # entries and this app's marker, because that is what `staged_entries()`
    # reads and what the helper is then told to move into the player's folder.
    scratch = layout.work_dir(install, layout.DOWNLOAD_NAME) / "unpack"
    try:
        _unpack_into(install, archive, scratch)
        built = (
            _the_one_yulon_directory(scratch) if install.kind is InstallKind.TARBALL else scratch
        )
        _require_the_executable(install, built)
        for entry in sorted(built.iterdir()):
            os.replace(entry, staged / entry.name)
    finally:
        discard(scratch)
    return staged


def _stage_appimage(archive: Path, staged: Path) -> None:
    """An AppImage is one file: the download IS the new build, renamed and made runnable.

    `os.replace` and not a copy: the download already landed inside the same
    marked directory, so this is a rename rather than 86 MB moved twice.
    """
    try:
        os.replace(archive, staged)
        os.chmod(staged, 0o755)
    except OSError as exc:
        raise UpdateError(f"The new AppImage could not be put in place: {exc}") from exc


def _unpack_into(install: Install, archive: Path, scratch: Path) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    if install.kind is InstallKind.TARBALL:
        _extract_tar(archive, scratch)
    else:
        _extract_zip(archive, scratch)


def _require_the_executable(install: Install, built: Path) -> None:
    if not (built / install.executable).is_file():
        raise UpdateError(
            f"That download does not contain {install.executable}, so it is not a build of "
            "Yu'lon for this computer. Nothing was installed."
        )


class _Budget:
    """Counts what an archive has really written, and refuses the moment it is too much.

    Counted from the FILES ON DISK rather than from the archive's declared
    sizes, so a header that lies is measured rather than believed.
    """

    def __init__(self) -> None:
        self.bytes = 0
        self.members = 0

    def add(self, path: Path) -> None:
        self.members += 1
        if self.members > MAX_MEMBERS:
            raise UpdateError(
                f"That download holds more than {MAX_MEMBERS} files, which is not a build "
                "of Yu'lon. Nothing was installed."
            )
        try:
            self.bytes += path.stat().st_size if path.is_file() else 0
        except OSError:  # a member the filter dropped; it wrote nothing
            return
        if self.bytes > MAX_UNPACKED_BYTES:
            raise UpdateError(
                f"That download unpacks to more than {MAX_UNPACKED_BYTES // (1024 * 1024)} MB, "
                "which is not a build of Yu'lon. Nothing was installed."
            )


def _extract_tar(archive: Path, into: Path) -> None:
    """`tarfile` with `filter="data"`, one member at a time, under a size budget.

    Measured on 3.13.15 against tarballs built for each shape: `../evil` raises
    `OutsideDestinationError`; a member linking to `../../../etc/passwd` raises
    `LinkOutsideDestinationError`; and `/etc/x` does **not** raise — the filter
    strips the leading slash and extracts it to `etc/x` inside the destination,
    where `_the_one_yulon_directory` then refuses the archive for not being a
    Yu'lon package. The executable bit survives and a setuid bit is stripped
    (`0o4755` came out `0o755`).

    One member at a time rather than `extractall`, so the budget is checked as
    the bytes land: `extractall` on a 300 KB archive that unpacks to gigabytes
    returns only once the disk is full.
    """
    _require_a_data_filter()
    budget = _Budget()
    try:
        with tarfile.open(archive) as handle:
            for member in handle:
                handle.extract(member, into, filter="data")
                budget.add(into / member.name)
    except UpdateError:
        raise
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
    """Every member name judged first, then one member at a time under the budget."""
    budget = _Budget()
    try:
        with zipfile.ZipFile(archive) as handle:
            infos = handle.infolist()
            for info in infos:
                if _names_somewhere_else(info.filename):
                    raise UpdateError(
                        f"That download contains a file that names somewhere else on this "
                        f"computer ({info.filename!r}). Nothing was installed."
                    )
            for info in infos:
                budget.add(Path(handle.extract(info, into)))
    except UpdateError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise UpdateError(f"The download could not be unpacked: {exc}") from exc


def _the_one_yulon_directory(scratch: Path) -> Path:
    """The archive's single top-level `yulon/`, or a refusal.

    A listing that decides a refusal and never a write: what it can say is "no"
    (`_NOT_A_PACKAGE`) or "here is the folder whose entries go up one level",
    and an unreadable folder is the same "no" — this is a directory this
    function's caller created itself, moments earlier.
    """
    try:
        entries = sorted(scratch.iterdir())
    except OSError as exc:
        raise UpdateError(f"The download could not be unpacked: {exc}") from exc
    if len(entries) != 1 or entries[0].name != TOP_LEVEL_DIR or not entries[0].is_dir():
        logger.info(f"self-update: the archive's top level is {[e.name for e in entries]}")
        raise UpdateError(_NOT_A_PACKAGE)
    return entries[0]


def staged_entries(install: Install, staged: Path) -> tuple[str, ...]:
    """The top-level names the STAGED build ships. **Exactly the build, nothing else.**

    The downloaded archive used to be in here — it was downloaded INTO the
    staging directory — so it came back from this function as one of the
    "entries the build ships", and the helper moved a 90 MB tarball into the
    player's folder as part of the program (cold review 2). It now lives in its
    own `.yulon-download` work dir, which is removed as soon as it has been
    unpacked, so the only thing this has to skip is the marker.
    """
    try:
        found = sorted(p.name for p in staged.iterdir() if p.name != layout.MARKER_NAME)
    except OSError as exc:
        raise UpdateError(f"The staged update could not be read: {exc}") from exc
    if not found:
        raise UpdateError("The staged update is empty. Nothing was installed.")
    bad = [name for name in found if not layout.is_entry_name(name)]
    if bad:  # pragma: no cover - extraction cannot produce one; the rule is stated anyway
        raise UpdateError(f"The staged update holds a name Yu'lon will not move: {bad[0]!r}")
    del install
    return tuple(found)


def entries_to_swap(install: Install, staged: Path) -> tuple[str, ...]:
    """The entries the helper will replace, **executable first**, or a refusal.

    Two rules, and the second is the one the data loss was about:

    * the executable comes FIRST, because the helper moves the current entries
      out in this order and brings the staged ones in reversed — so the
      executable is the first thing to leave and the last thing to arrive, and
      a half-done swap is "no executable" rather than "the new executable on
      the old libraries";
    * a staged entry may collide with something already in the install folder
      only if the RUNNING build shipped that name. A `README.txt` or a
      `thesis.docx` beside the executable is the player's, and an update that
      moved it aside would be the defect this design removed. That refuses the
      whole update, and the dialog falls back to downloading the file.
    """
    target = install.target
    if target is None:
        raise UpdateError("Yu'lon cannot replace this install by itself.")
    ours = layout.shipped_entries(install)
    found = staged_entries(install, staged)
    trespass = [name for name in found if (target / name).exists() and name not in ours]
    if trespass:
        raise UpdateError(
            f"The new version would have to replace {trespass[0]!r} in your Yu'lon folder, "
            "and Yu'lon did not put that there. Nothing was changed — download the new "
            "version and install it into a folder of its own."
        )
    rest = sorted(name for name in found if name != install.executable)
    return (install.executable, *rest) if install.executable in found else tuple(rest)


def staged_executable(install: Install, staged: Path) -> Path:
    """What to run to prove the staged build opens, inside its work directory."""
    if install.kind is InstallKind.APPIMAGE:
        return staged / layout.APPIMAGE_ENTRY
    return staged / install.executable


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
        cwd=str(Path(argv[0]).parent),
        creationflags=runner.creationflags(),
    )
    return completed.returncode


def smoke_test(exe: Path, *, run: RunSmoke = _run, timeout: float = SMOKE_TIMEOUT_SECONDS) -> None:
    """Run the staged build once under `YULON_SMOKE_TEST` and require exit 0.

    `main()` answers that variable by building the whole window and returning 0
    without entering the event loop, so this proves the thing a packaging fault
    actually breaks: that the bundle's Qt, its plugins and its data files are
    all present on THIS machine. It is the last check before the running
    install is touched.

    **What the smoke run must NOT do is the other half of this**, and `main.py`
    enforces it: under that variable the app starts no update check, writes no
    `update.json` or `state.json`, and removes no `.yulon-old`. The staged
    build runs with the old tree still in place and the player's real config
    directory in reach, so a smoke test with side effects is a check that
    changes what it is checking.

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
