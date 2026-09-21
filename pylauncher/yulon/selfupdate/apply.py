"""The sequence: prove the release, download it, unpack it, run it, plan the swap.

One function, six steps, and a rule that holds at every one of them: **if this
stops for any reason, the running install is byte for byte what it was and
nothing this call staged is still on disk.** `test_selfupdate_apply.py` hashes
the install tree before and after every refusal and every cancel.

Nothing here runs a process, opens a socket or touches Qt. Each step is a seam
on `ApplyIO` whose default is the real function, so the order and the hand-offs
are what the tests are about.

The two outcomes are deliberately different types rather than a flag:
`ReadyToRestart` means the helper is written and the app should close, and
`SavedForManualInstall` means a verified file is sitting in the player's
downloads folder and nothing else has happened.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from yulon.log import get_logger
from yulon.selfupdate import fetch, swap
from yulon.selfupdate import stage as stage_module
from yulon.selfupdate.detect import Install
from yulon.selfupdate.fetch import Cancelled, IsCancelled, Progress, UpdateError
from yulon.selfupdate.stage import sibling
from yulon.selfupdate.swap import SwapPlan
from yulon.update import CHECKSUMS_NAME, UpdateCheck

logger = get_logger(__name__)

MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
"""The largest artifact this app will fetch, as a sanity bound on a number off the feed.

The size the read is bounded by comes from the releases API, which is remote
text like everything else in that document. The biggest thing this project
ships is an AppImage of about 80 MB; half a gigabyte is six times that and
still not a disk somebody else gets to choose to fill.
"""

CHECKING = "Checking…"
DOWNLOADING = "Downloading…"
UNPACKING = "Unpacking…"
TESTING = "Testing the new version…"
READY = "Ready"

StageChanged = Callable[[str], None]
"""Told which of the five steps is running, for the progress dialog's label."""


@dataclass(frozen=True)
class ReadyToRestart:
    """The new build is staged, proved, and a helper is written. Close the window now."""

    plan: SwapPlan
    version: str


@dataclass(frozen=True)
class SavedForManualInstall:
    """A verified file in the player's downloads folder. Nothing else happened."""

    path: Path
    version: str


def _real_script_dir() -> Path:
    """Where the helper is written. Imported lazily so nothing is decided at import time."""
    import tempfile

    return Path(tempfile.gettempdir())


@dataclass(frozen=True)
class ApplyIO:
    """The seams, defaulting to the real functions.

    A frozen dataclass whose fields are callables: the generated `__init__`
    assigns every one of them onto the INSTANCE, so `io.download` is the plain
    function and never a bound method of this object.
    """

    fetch_text: Callable[[str], str] = field(default=fetch.fetch_text)
    download: Callable[..., Path] = field(default=fetch.download)
    verify: Callable[[Path, str], None] = field(default=fetch.verify)
    stage: Callable[[Install, Path], Path] = field(default=stage_module.stage)
    smoke_test: Callable[[Path], None] = field(default=stage_module.smoke_test)
    plan_swap: Callable[..., SwapPlan] = field(default=swap.plan_swap)
    script_dir: Callable[[], Path] = field(default=_real_script_dir)


REAL_IO = ApplyIO()
"""The real functions, as one value, so `apply_update`'s default is not a call.

It is immutable and holds no state, so one shared instance is the whole of it.
"""


def apply_update(
    result: UpdateCheck,
    install: Install,
    *,
    downloads_dir: Path,
    pid: int,
    progress: Progress,
    stage_changed: StageChanged,
    cancelled: IsCancelled,
    io: ApplyIO = REAL_IO,
) -> ReadyToRestart | SavedForManualInstall:
    """Do the update, or refuse in a sentence the player can read.

    Raises `UpdateError` for every refusal and `Cancelled` when the player
    pressed Cancel. Both leave the install untouched and remove what was
    staged.
    """
    name, size = _what_to_ask_for(result, install)
    tag = str(result.latest)
    _stop_if_cancelled(cancelled)

    if install.can_swap and install.target is not None:
        return _swap_path(
            result=result,
            install=install,
            target=install.target,
            name=name,
            size=size,
            tag=tag,
            pid=pid,
            progress=progress,
            stage_changed=stage_changed,
            cancelled=cancelled,
            io=io,
        )
    return _download_only(
        name=name,
        size=size,
        tag=tag,
        into=downloads_dir,
        progress=progress,
        stage_changed=stage_changed,
        cancelled=cancelled,
        io=io,
    )


def _what_to_ask_for(result: UpdateCheck, install: Install) -> tuple[str, int]:
    """The artifact's name and its declared size, or a refusal — **before any network call**.

    Three separate refusals, all of them offline, and the order matters only in
    that none of them costs a request: nothing newer on offer; a release with
    no `SHA256SUMS`; and a release whose assets do not carry the name this
    machine computed from the tag.
    """
    if not result.available or not result.latest:
        raise UpdateError("There is no newer version to install.")
    if not result.has_checksums:
        raise UpdateError(
            "This release does not publish a SHA256SUMS file, so Yu'lon cannot prove a "
            "download of it. Use the release page instead."
        )
    name = install.artifact_name(result.latest)
    if name is None:
        raise UpdateError(
            "Yu'lon does not know how to update this kind of install by itself. "
            "Use the release page instead."
        )
    found = [asset for asset in result.assets if asset.name == name]
    if not found:
        raise UpdateError(
            f"This release does not carry {name}, so there is nothing for this computer "
            "to install. Use the release page instead."
        )
    size = found[0].size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise UpdateError(
            f"This release gives {name} a size Yu'lon will not accept ({size} bytes). "
            "Use the release page instead."
        )
    return name, size


def _stop_if_cancelled(cancelled: IsCancelled) -> None:
    """Checked between every step, so Cancel is never more than one step away."""
    if cancelled():
        raise Cancelled("The update was cancelled.")


def _digest_for(tag: str, name: str, io: ApplyIO, stage_changed: StageChanged) -> str:
    """Fetch `SHA256SUMS` and take this artifact's line out of it.

    The URL is built by `fetch.artifact_url` from the repository constant, not
    read off the asset list: the feed's `browser_download_url` is remote text,
    and the whole proof rests on this file being the release's own.
    """
    stage_changed(CHECKING)
    return fetch.expected_digest(io.fetch_text(fetch.artifact_url(tag, CHECKSUMS_NAME)), name)


def _fetch_and_verify(
    *,
    tag: str,
    name: str,
    size: int,
    dest: Path,
    digest: str,
    progress: Progress,
    stage_changed: StageChanged,
    cancelled: IsCancelled,
    io: ApplyIO,
) -> Path:
    """Download to `dest` and refuse unless it hashes to `digest`."""
    stage_changed(DOWNLOADING)
    archive = io.download(
        fetch.artifact_url(tag, name),
        dest,
        expected_size=size,
        progress=progress,
        cancelled=cancelled,
    )
    _stop_if_cancelled(cancelled)
    io.verify(archive, digest)
    _stop_if_cancelled(cancelled)
    return archive


def _swap_path(
    *,
    result: UpdateCheck,
    install: Install,
    target: Path,
    name: str,
    size: int,
    tag: str,
    pid: int,
    progress: Progress,
    stage_changed: StageChanged,
    cancelled: IsCancelled,
    io: ApplyIO,
) -> ReadyToRestart:
    """Download beside the install, unpack, prove it opens, and write the helper.

    The download goes to `<target>.download/` and not to the player's downloads
    folder, because the swap is a RENAME and a rename only works on one
    filesystem: `<target>.download`, `<target>.new` and `<target>` are all
    siblings by construction.

    `except BaseException` and not `except Exception`: a `Cancelled`, a
    `KeyboardInterrupt` and a `SystemExit` all leave the same rubbish beside
    the install, and the promise this package makes is about what is on disk
    afterwards rather than about which exception class got there.
    """
    del result  # judged already by `_what_to_ask_for`
    downloads = sibling(target, ".download")
    staged_at = sibling(target, ".new")
    unpack = sibling(target, ".new-unpack")
    try:
        digest = _digest_for(tag, name, io, stage_changed)
        _stop_if_cancelled(cancelled)
        archive = _fetch_and_verify(
            tag=tag,
            name=name,
            size=size,
            dest=downloads / name,
            digest=digest,
            progress=progress,
            stage_changed=stage_changed,
            cancelled=cancelled,
            io=io,
        )
        stage_changed(UNPACKING)
        staged = io.stage(install, archive)
        _stop_if_cancelled(cancelled)
        stage_changed(TESTING)
        io.smoke_test(stage_module.staged_executable(install, staged))
        _stop_if_cancelled(cancelled)
        plan = io.plan_swap(install, staged, pid=pid, script_dir=io.script_dir())
    except BaseException:
        stage_module.discard(downloads, staged_at, unpack)
        raise
    # The archive has done its job; what the helper renames is `<target>.new`.
    stage_module.discard(downloads)
    stage_changed(READY)
    logger.info(f"self-update: {tag} is staged at {staged} and the helper is written")
    return ReadyToRestart(plan, tag)


def _download_only(
    *,
    name: str,
    size: int,
    tag: str,
    into: Path,
    progress: Progress,
    stage_changed: StageChanged,
    cancelled: IsCancelled,
    io: ApplyIO,
) -> SavedForManualInstall:
    """macOS, and any install this user cannot write: download, prove, and stop.

    The file is still checked against `SHA256SUMS` — a download the player is
    about to run by hand deserves the same proof as one this app would install
    — and then it is theirs.
    """
    digest = _digest_for(tag, name, io, stage_changed)
    _stop_if_cancelled(cancelled)
    saved = _fetch_and_verify(
        tag=tag,
        name=name,
        size=size,
        dest=into / name,
        digest=digest,
        progress=progress,
        stage_changed=stage_changed,
        cancelled=cancelled,
        io=io,
    )
    stage_changed(READY)
    logger.info(f"self-update: {tag} saved to {saved} for the player to install")
    return SavedForManualInstall(saved, tag)
