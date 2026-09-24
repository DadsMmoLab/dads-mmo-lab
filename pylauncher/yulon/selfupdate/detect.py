"""What kind of install is running, what artifact it wants, and what it may do.

One question with five answers, and everything downstream is decided by it: an
AppImage replaces a FILE, a tarball and a Windows zip replace a FOLDER, a macOS
app is downloaded and installed by hand, a checkout is sent to the release
page, and a machine this project builds nothing for is sent there too.

**The artifact name is computed here from the tag and never read off the feed.**
The release's asset list is remote text; the name the app asks for is one this
file spells. `test_the_names_really_are_the_ones_release_yml_writes` pins the
four suffixes against `.github/workflows/release.yml`, because a drift between
the two is invisible — the app would simply decide the release has nothing for
this machine, on one platform, silently.

Every fact is injectable, because the alternative is a test that patches
`sys.frozen` or `sys.executable` for the whole process.
"""

from __future__ import annotations

import enum
import os
import platform as _platform
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from yulon import platform
from yulon.log import get_logger
from yulon.update import UpdateCheck

logger = get_logger(__name__)


class InstallKind(enum.Enum):
    """How this copy of Yu'lon was delivered."""

    APPIMAGE = "appimage"
    TARBALL = "tarball"
    WINDOWS_ZIP = "windows_zip"
    MACOS_APP = "macos_app"
    SOURCE = "source"
    UNSUPPORTED = "unsupported"


_X86_64 = frozenset({"x86_64", "amd64"})
"""The one machine this project builds for, in both of its spellings.

`platform.machine()` answers `x86_64` on Linux and macOS and `AMD64` on
Windows, and they are the same processor.
"""

_SUFFIX: dict[InstallKind, str] = {
    InstallKind.APPIMAGE: "-x86_64.AppImage",
    InstallKind.TARBALL: "-x86_64.tar.gz",
    InstallKind.WINDOWS_ZIP: "-windows-x64.zip",
    InstallKind.MACOS_APP: "-macos.dmg",
}
"""What `release.yml` calls each artifact, after `Yulon-<tag>`."""

_SWAPPABLE = frozenset({InstallKind.APPIMAGE, InstallKind.TARBALL, InstallKind.WINDOWS_ZIP})
"""The kinds a helper script can rename into place. macOS is not one of them."""


@dataclass(frozen=True)
class Install:
    """This copy of Yu'lon: what it is, what would be replaced, and whether that is possible."""

    kind: InstallKind
    target: Path | None
    """What a swap replaces: the AppImage FILE, or the one-dir FOLDER. None where nothing is."""
    executable: str
    """What to launch inside a folder target (`yulon` / `yulon.exe`); `""` for a file target."""
    writable: bool
    """A sibling can be created beside `target`, so a rename into place can work."""

    def artifact_name(self, tag: str) -> str | None:
        """`Yulon-<tag><suffix>` for the kinds that have an artifact, else None."""
        suffix = _SUFFIX.get(self.kind)
        return None if suffix is None else f"Yulon-{tag}{suffix}"

    @property
    def can_swap(self) -> bool:
        """Whether this install can be replaced in place by the helper."""
        return self.kind in _SWAPPABLE and self.writable and self.target is not None


def _probe(directory: Path) -> bool:
    """Can a file be created in `directory`? **Tried, not asked.**

    `os.access` lies: on Windows it reports the read-only ATTRIBUTE and knows
    nothing about the ACL that actually decides, and on a network mount it
    answers from the local credentials rather than from the server. The only
    honest way to ask is to make a file and remove it.

    **Which directory is asked changed in cold review 1.** It used to be the
    target's PARENT, because the swap renamed the whole install folder; since
    the swap now works on the entries inside the folder and stages into
    `<target>/.yulon-new`, the folder that has to accept a new file is the
    target itself. An AppImage is still a file with siblings, so that one asks
    its parent — `detect_install` passes whichever applies.
    """
    try:
        handle, name = tempfile.mkstemp(prefix=".yulon-update-probe-", dir=directory)
    except OSError as exc:
        logger.info(f"self-update: {directory} does not take a new file ({exc})")
        return False
    os.close(handle)
    try:
        os.unlink(name)
    except OSError as exc:  # pragma: no cover - a file we just made, in a dir we just wrote
        logger.info(f"self-update: could not remove the probe file {name} ({exc})")
    return True


def detect_install(
    *,
    frozen: bool | None = None,
    platform_id: str | None = None,
    machine: str | None = None,
    environ: Mapping[str, str] | None = None,
    executable: Path | None = None,
    probe_writable: Callable[[Path], bool] = _probe,
) -> Install:
    """Which of the five kinds this process is, measured rather than configured.

    Nothing about this may be overridden by a setting or an environment
    variable of ours: where an update comes from, and what it replaces, are
    facts about the running process. `$APPIMAGE` is read because it is the
    AppImage runtime's own way of telling a payload where its file is — and it
    is believed only when it names a file that exists, since an unrelated
    AppImage earlier in the session leaves it set in the environment a child
    inherits.
    """
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return Install(InstallKind.SOURCE, None, "", False)
    which = platform_id or platform.detect()
    arch = (machine if machine is not None else _platform.machine()).lower()
    env = os.environ if environ is None else environ
    exe = Path(executable if executable is not None else sys.executable)
    if which == "macos":
        # arm64 only: `release.yml` builds the dmg on an Apple-silicon runner
        # and `universal2` is blocked by pydantic-core. An Intel Mac gets the
        # release page and no artifact.
        kind = InstallKind.MACOS_APP if arch == "arm64" else InstallKind.UNSUPPORTED
        return Install(kind, None, "", False)
    if arch not in _X86_64:
        return Install(InstallKind.UNSUPPORTED, None, "", False)
    # The folder the executable sits in — which is NOT assumed to be a folder
    # this app owns. It may be the player's Downloads folder, if that is where
    # they unpacked the zip. What may be replaced inside it is decided by
    # `layout.shipped_entries()`, never by this function.
    if which == "windows":
        return Install(InstallKind.WINDOWS_ZIP, exe.parent, exe.name, probe_writable(exe.parent))
    appimage = env.get("APPIMAGE")
    if appimage and Path(appimage).is_file():
        target = Path(appimage)
        # A file install stages beside itself, so the PARENT is what has to
        # take a new file; a folder install stages inside itself.
        return Install(InstallKind.APPIMAGE, target, "", probe_writable(target.parent))
    return Install(InstallKind.TARBALL, exe.parent, exe.name, probe_writable(exe.parent))


UPDATE_NOW = "Update now"
DOWNLOAD = "Download"
OPEN_PAGE = "Open download page"


def action_label(install: Install, result: UpdateCheck) -> str:
    """What the dialog's action button says, which is also what pressing it does.

    Three answers and one rule: an update is installed only when it can be
    PROVED and the install can be replaced. No checksums, or a release whose
    assets do not carry the name this machine computed, and the button becomes
    the release page — the player is never quietly given a download this app
    would not verify.
    """
    name = install.artifact_name(result.latest) if result.latest else None
    listed = name is not None and any(asset.name == name for asset in result.assets)
    if not result.has_checksums or not listed:
        return OPEN_PAGE
    return UPDATE_NOW if install.can_swap else DOWNLOAD
