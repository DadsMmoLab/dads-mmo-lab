"""The process install engine: a server built on the host and run as host programs.

The catalog's single servers come in two runtimes (`catalog.SingleInstall`). The
container one is `single_install.py`; this is the other (`catalog.SingleProcess`),
written for DFO's 115US server, whose Windows programs answer on two loopback
addresses at once (127.0.0.1:7001 and 127.0.0.2 at a port the game server picks),
which no published container port can stand in for. Nothing here touches Docker or
git: the source at its pinned commit and the Go toolchain both arrive as zips
checked against a size and a sha256 measured once, so the host needs neither.

What it shares with the container engine is taken from the same places, so it
cannot drift:

* the state file, its claim and the record checks — `native.InstallState`,
  `read_state()`/`write_state()`, `single_install._read_record()`;
* the install id — `composegen.install_id()`;
* the patch apply — `families/patch.py`, the tolerant, all-or-nothing apply the
  CMaNGOS family uses;
* the download — `platform.download_verified()`;
* the streamed child — `runner.stream()`, which the log panel's Stop ends.

Four recorded, resumable stages. There is no `up` and no `ready`: the server reads
the player's client when it starts (DFO builds its inner PVF from it), and Install
asks for no client, so the first Start on the server's tab is where it first runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import threading
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from yulon import platform, resources, runner
from yulon.catalog import composegen, native
from yulon.catalog.catalog import SingleEntry, SingleProcess, VerifiedArchive
from yulon.catalog.families import patch
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    InstallStopped,
    UnsupportedPlatformError,
    unsupported_platform_message,
)
from yulon.catalog.single_install import _read_record
from yulon.log import get_logger
from yulon.ui import lines

logger = get_logger(__name__)

STAGE_NAMES: tuple[str, ...] = (
    "fetch-source",
    "patch-sources",
    "fetch-toolchain",
    "build",
)
"""The stages, in order. What `read_state()` validates a state file's `completed` against."""

DOWNLOADS = ".yulon-downloads"
"""Where the zips are fetched to, under the server dir; each is deleted once unpacked."""

UNPACKED = ".yulon-archive.json"
"""The note an unpacked folder carries: which archive it is, by URL and sha256.

What lets a resumed install tell its own finished unpack from a folder somebody
else put there, without trusting the state file alone.
"""

PARTIAL_SUFFIX = ".yulon-partial"
"""An unpack lands in `<folder>.yulon-partial` and is renamed into place when complete,
so a stopped unpack never leaves a folder that looks finished."""

PROGRESS_EVERY = 1000
"""Files unpacked between two progress lines."""

CHUNK = 1 << 20


def toolchain_paths(server_dir: Path, process: SingleProcess) -> dict[str, Path]:
    """Where the toolchain, its module cache and its build cache live for this install.

    All inside the server dir, beside the unpacked Go, so removing the folder removes
    everything the build fetched and nothing of the player's own Go setup is read or
    written.
    """
    go_root = server_dir / process.toolchain.dest
    work = go_root.parent
    return {
        "go": go_root / "bin" / ("go.exe" if os.name == "nt" else "go"),
        "gopath": work / "gopath",
        "gocache": work / "gocache",
    }


def go_env(server_dir: Path, process: SingleProcess) -> dict[str, str]:
    """The build's whole environment: this process's, with Go pointed at the install's own.

    `GOTOOLCHAIN=local` so the pinned Go never fetches another; `GOENV=off` so the
    player's own `go env -w` settings are not read; `-modcacherw` so the module cache
    can be deleted with the folder; `CGO_ENABLED=0` because the server's SQLite is
    pure Go and a C compiler is not something to ask a player for. `GOROOT` is taken
    out, so one the player set for another Go cannot redirect this one.
    """
    paths = toolchain_paths(server_dir, process)
    # `runner.child_env()`: in a frozen build (the AppImage) the bundle's own
    # LD_LIBRARY_PATH must not reach Go or anything Go runs.
    inherited = runner.child_env(dict(os.environ)) or dict(os.environ)
    env = {key: value for key, value in inherited.items() if key.upper() != "GOROOT"}
    env.update(
        {
            "GOTOOLCHAIN": "local",
            "GOENV": "off",
            "GOFLAGS": "-modcacherw",
            "CGO_ENABLED": "0",
            "GOPATH": str(paths["gopath"]),
            "GOMODCACHE": str(paths["gopath"] / "pkg" / "mod"),
            "GOCACHE": str(paths["gocache"]),
        }
    )
    return env


def _is_tarball(url: str) -> bool:
    """Whether an archive is a gzipped tar (Go's Linux release) rather than a zip."""
    return url.endswith((".tar.gz", ".tgz"))


def sha256_of(path: Path) -> str:
    """The file's sha256, read in chunks: the archives are tens of megabytes."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def unpacked_from(folder: Path) -> str | None:
    """The sha256 of the archive `folder` was unpacked from, or None if it is not one of ours."""
    try:
        note = json.loads((folder / UNPACKED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = note.get("sha256") if isinstance(note, dict) else None
    return value if isinstance(value, str) else None


def existing_install(
    entry: SingleEntry, server_dir: Path, *, platform_id: Callable[[], str] = platform.detect
) -> native.InstallState:
    """The finished install in `server_dir`, for "Use existing…"; refuse anything else.

    `single_install.existing_install()`'s rule over this engine's stages: the record
    must be there, be this entry's and this folder's, and have every stage finished.
    """
    if not (server_dir / native.STATE_FILE).is_file():
        raise InstallerError(
            f"{server_dir} holds no {entry.name} server that Yu'lon installed. Pick the folder "
            f"Install made, or press Install."
        )
    state = _read_record(
        entry,
        server_dir,
        composegen.install_id(server_dir, platform_id=platform_id),
        stages=STAGE_NAMES,
    )
    unfinished = [stage for stage in STAGE_NAMES if not state.has(stage)]
    if unfinished:
        raise InstallerError(
            f"The {entry.name} install in {server_dir} did not finish (it stopped before "
            f"{unfinished[0]}). Press Install and pick this folder to finish it."
        )
    return state


def cancelled_message(entry: SingleEntry, server_dir: Path) -> str:
    """What Stop left, said plainly, and which button to press next."""
    return (
        f"The {entry.name} install was stopped, so it has not been remembered. Nothing was "
        f"undone: whatever had been downloaded, unpacked or built is still in {server_dir}. "
        "Press Install again and choose the same folder to carry on from where it stopped, or "
        "delete the folder to start over."
    )


def finished_message(entry: SingleEntry, server_dir: Path) -> str:
    """The last line of a finished install: what it did, and the one thing left to do."""
    play = entry.play
    client = play.client if play is not None else "your client"
    return (
        f"{entry.name} is built in {server_dir}. On its tab, choose {client}, then press "
        "Start: the first start reads your client to prepare the game data, and can take "
        "several minutes."
    )


@dataclass(frozen=True)
class ProcessSeams:
    """Everything the process engine reaches outside itself through. Real by default.

    For `native.Seams`' reason: an engine whose every external effect is a seam can
    have its control flow tested without a network or a compiler.
    """

    platform_id: Callable[[], str] = platform.detect
    dir_problem: Callable[[Path], str | None] = platform.server_dir_problem
    download: Callable[[str, Path], Path] = platform.download_verified
    stream: Callable[..., Iterator[str]] = runner.stream


class ProcessInstaller:
    """Install a `process` single server: fetch, patch, fetch the toolchain, build."""

    def __init__(
        self,
        entry: SingleEntry,
        *,
        installers_root: Path | None = None,
        seams: ProcessSeams | None = None,
    ) -> None:
        process = entry.install.process
        if process is None:
            raise ValueError(f"{entry.id} is not built as host programs")
        self.entry = entry
        self.process = process
        self.installers_root = (
            installers_root if installers_root is not None else resources.installers_dir()
        )
        self._seams = seams if seams is not None else ProcessSeams()
        self._patched = False
        """Whether this press applied a patch, which makes the build run again."""

    # -- identity ----------------------------------------------------------------

    def server_dir(self, options: InstallOptions) -> Path:
        """Where this install goes: what the user picked, or the entry's folder under $HOME."""
        if options.server_dir is not None:
            return options.server_dir
        return Path.home() / self.entry.install.default_server_dir

    def install_id(self, server_dir: Path) -> str:
        """The install id the state file carries (`composegen.install_id`)."""
        return composegen.install_id(server_dir, platform_id=self._seams.platform_id)

    # -- the InstallEngine half a Catalog tile drives ----------------------------

    def preflight(
        self,
        options: InstallOptions,
        cancel: threading.Event | None = None,
        *,
        ask: runner.Prompter | None = None,
    ) -> None:
        """Everything that must be true before anything is written. Raises, or returns."""
        self._preflight(options)

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        ask: runner.Prompter | None = None,
    ) -> Iterator[str]:
        """Run the install, yielding output live. Resumes whatever a previous run finished.

        Raises:
            InstallerError: any refusal, any stage that failed, or a Stop. The message
                is the sentence a user reads in the failure dialog.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        yield f"Installing {self.entry.name} into {server_dir}"
        self._preflight(opts)
        self._check_cancel(cancel, server_dir)
        state = self._claim(server_dir)
        yield f"Using {server_dir} ({'resuming' if state.completed else 'a fresh install'})"
        if state.completed:
            yield f"Already finished: {', '.join(state.completed)}"
        server_dir.mkdir(parents=True, exist_ok=True)
        native.write_state(server_dir, state)
        self._patched = False
        bodies: dict[
            str, Callable[[Path, native.InstallState, threading.Event | None], Iterator[str]]
        ]
        bodies = {
            "fetch-source": self._fetch_source,
            "patch-sources": self._patch_sources,
            "fetch-toolchain": self._fetch_toolchain,
            "build": self._build,
        }
        try:
            total = len(STAGE_NAMES)
            for number, name in enumerate(STAGE_NAMES, start=1):
                self._check_cancel(cancel, server_dir)
                # The spine's two lines, in its format (`StagedInstaller._staged()`).
                yield f"Step {number} of {total} ({number * 100 // total}%): {name}"
                yield f"--- {name}"
                yield from bodies[name](server_dir, state, cancel)
                state = state.with_stage(name, STAGE_NAMES)
                native.write_state(server_dir, state)
        except InstallerError as exc:
            native.write_state(
                server_dir,
                replace(state, last_error=str(exc), error_run=native.ERROR_RUN_INSTALL),
            )
            raise
        native.write_state(server_dir, replace(state, last_error="", error_run=""))
        logger.info(f"install of {self.entry.id} finished")
        yield finished_message(self.entry, server_dir)

    # -- preflight and the folder's claim ----------------------------------------

    def _preflight(self, options: InstallOptions) -> None:
        here = self._seams.platform_id()
        if not self.entry.install.supports(here):
            raise UnsupportedPlatformError(unsupported_platform_message(self.entry, here))
        server_dir = self.server_dir(options)
        folder_problem = self._seams.dir_problem(server_dir)
        if folder_problem is not None:
            raise InstallerError(
                f"{folder_problem} Pick a different folder and try again. Nothing was written."
            )
        self._claim(server_dir)

    def _claim(self, server_dir: Path) -> native.InstallState:
        """This folder's state, or a new one; refuse a folder that is not ours to fill."""
        expected = self.install_id(server_dir)
        if (server_dir / native.STATE_FILE).is_file():
            return _read_record(self.entry, server_dir, expected, stages=STAGE_NAMES)
        if server_dir.is_dir():
            leftovers = sorted(native._listing(server_dir, ignoring=native.OUR_OWN_FILES))
            if leftovers:
                raise InstallerError(
                    f"{server_dir} already has files in it ({', '.join(leftovers[:3])}"
                    f"{', …' if len(leftovers) > 3 else ''}), so nothing was installed there. "
                    "Pick an empty folder or a new one. Nothing was written."
                )
        return native.InstallState(
            game_id=self.entry.id, install_id=expected, family=self.process.family
        )

    @staticmethod
    def _stopped(cancel: threading.Event | None) -> bool:
        return cancel is not None and cancel.is_set()

    def _check_cancel(self, cancel: threading.Event | None, server_dir: Path) -> None:
        if self._stopped(cancel):
            raise InstallStopped(cancelled_message(self.entry, server_dir))

    # -- the stages ---------------------------------------------------------------

    def _fetch_source(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """The pinned source, downloaded and unpacked at `checkout`, once."""
        source = next(s for s in self.entry.emulator.sources if s.dest == self.process.checkout)
        yield f"{source.repo} at {source.rev}"
        yield from self._fetch_and_unpack(
            self.process.archive, server_dir / self.process.checkout, server_dir, cancel
        )

    def _patch_sources(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Apply every patch, on every press: one already in place is said and left alone.

        Run whether or not the record says this stage finished, for the CMaNGOS
        family's reason: the files are the truth, and a patch a newer Yu'lon added
        must reach a folder an older one built. Anything applied this press makes
        the build run again.
        """
        root = server_dir / self.process.checkout
        here = self._seams.platform_id()
        for entry_patch in self.process.patches:
            if entry_patch.platforms and here not in entry_patch.platforms:
                continue
            path = self.installers_root / entry_patch.file
            name = PurePosixPath(entry_patch.file).name
            try:
                text = path.read_text(encoding="utf-8")
                results = patch.apply(text, root, name=name)
            except OSError as exc:
                raise InstallerError(f"{name} could not be read: {exc}") from exc
            except patch.PatchError as exc:
                raise InstallerError(str(exc)) from exc
            if any(result.applied for result in results):
                self._patched = True
                yield f"Applied {name}: {entry_patch.reason}"
            else:
                yield f"{name} is already in place."

    def _fetch_toolchain(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """The pinned Go, downloaded and unpacked at `toolchain.dest`, once."""
        toolchain = self.process.toolchain_for(self._seams.platform_id())
        yield f"{toolchain.name}, for this server only (inside {server_dir})"
        yield from self._fetch_and_unpack(
            toolchain, server_dir / toolchain.dest, server_dir, cancel
        )

    def _build(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """`go build` each program, unless a recorded build's programs are all still there."""
        module = server_dir / self.process.module
        outputs = [module / build.output for build in self.process.build]
        if state.has("build") and not self._patched and all(p.is_file() for p in outputs):
            yield "The server is already built; skipping the build."
            return
        go = toolchain_paths(server_dir, self.process)["go"]
        env = go_env(server_dir, self.process)
        yield (
            "Building the server. The first build downloads the Go modules it needs and "
            "takes a few minutes; the output below is live."
        )
        for build, output in zip(self.process.build, outputs, strict=True):
            self._check_cancel(cancel, server_dir)
            yield f"go build {build.package} -> {build.output}"
            argv = [str(go), "build", "-trimpath", "-o", build.output, build.package]
            tail: list[str] = []
            try:
                for line in self._seams.stream(argv, module, merge_stderr=True, env=env):
                    tail = [*tail[-11:], line]
                    yield from lines.relayed(line, stage="build")
            except runner.StreamEnded as exc:
                raise InstallStopped(cancelled_message(self.entry, server_dir)) from exc
            except subprocess.CalledProcessError as exc:
                if self._stopped(cancel):
                    raise InstallStopped(cancelled_message(self.entry, server_dir)) from exc
                raise InstallerError(
                    f"{build.package} did not build (exit {exc.returncode}). Its last lines:\n"
                    + "\n".join(tail)
                ) from exc
            except OSError as exc:
                raise InstallerError(f"Go could not be started from {go}: {exc}") from exc
            if not output.is_file():
                raise InstallerError(
                    f"go build said it finished, but {output} is not there. Nothing else was "
                    "changed; press Install again to retry the build."
                )
        yield "The build finished."

    # -- downloads -----------------------------------------------------------------

    def _fetch_and_unpack(
        self,
        archive: VerifiedArchive,
        dest: Path,
        server_dir: Path,
        cancel: threading.Event | None,
    ) -> Iterator[str]:
        """Download `archive`, check it, and unpack its `root` at `dest`; reuse a finished one.

        A `dest` this engine unpacked from the same sha256 is left exactly as it is
        (the patches may already be in it). Anything else at `dest` is refused, never
        replaced: the folder was claimed empty, so a stranger there is somebody's.
        """
        if unpacked_from(dest) == archive.sha256:
            yield f"{dest.name} is already unpacked; leaving it exactly as it is."
            return
        if dest.exists() and native._listing(dest):
            raise InstallerError(
                f"{dest} has files in it that Yu'lon did not unpack from {archive.url}, so it was "
                "left alone. Move that folder aside and try again."
            )
        zip_path = server_dir / DOWNLOADS / PurePosixPath(archive.url).name
        yield from self._download(archive, zip_path)
        self._check_cancel(cancel, server_dir)
        yield from self._unpack(archive, zip_path, dest, server_dir, cancel)
        try:
            zip_path.unlink()
            (server_dir / DOWNLOADS).rmdir()
        except OSError:
            pass  # a folder still holding the other download, or one Windows has open

    def _download(self, archive: VerifiedArchive, zip_path: Path) -> Iterator[str]:
        """Fetch `archive` to `zip_path` and refuse it unless its size and sha256 are the pin's.

        A file already at `zip_path` that fails the check is deleted and fetched once
        more: `download_verified()` reuses a completed file as it finds it, and a
        download cut off by a lost connection is complete by its own lights.
        """
        for attempt in (1, 2):
            if not zip_path.is_file():
                yield f"Downloading {archive.url}"
            try:
                self._seams.download(archive.url, zip_path)
            except OSError as exc:
                raise InstallerError(f"{archive.url} could not be downloaded: {exc}") from exc
            size = zip_path.stat().st_size
            if size == archive.size and sha256_of(zip_path) == archive.sha256:
                yield f"{zip_path.name} matches its pinned size and sha256."
                return
            zip_path.unlink(missing_ok=True)
            if attempt == 2:
                raise InstallerError(
                    f"{archive.url} did not match the size and sha256 this version of Yu'lon "
                    f"pins ({size} bytes, expected {archive.size}). It was deleted and nothing "
                    "was unpacked."
                )
            yield "The download did not match its pinned sha256; fetching it again."

    def _unpack(
        self,
        archive: VerifiedArchive,
        zip_path: Path,
        dest: Path,
        server_dir: Path,
        cancel: threading.Event | None,
    ) -> Iterator[str]:
        """Unpack the archive's `root` into `<dest>.yulon-partial`, then rename it to `dest`."""
        partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
        if partial.exists():
            shutil.rmtree(partial)
        try:
            if _is_tarball(archive.url):
                yield from self._unpack_tar(archive, zip_path, partial, dest, server_dir, cancel)
            else:
                yield from self._unpack_zip(archive, zip_path, partial, dest, server_dir, cancel)
            (partial / UNPACKED).write_text(
                json.dumps({"url": archive.url, "sha256": archive.sha256}, indent=2) + "\n",
                encoding="utf-8",
            )
            if dest.is_dir():
                dest.rmdir()  # empty, `_fetch_and_unpack` checked; Windows renames onto nothing
            partial.replace(dest)
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
            raise InstallerError(f"{zip_path.name} could not be unpacked: {exc}") from exc
        yield f"Unpacked into {dest.relative_to(server_dir)}."

    def _unpack_zip(
        self,
        archive: VerifiedArchive,
        zip_path: Path,
        partial: Path,
        dest: Path,
        server_dir: Path,
        cancel: threading.Event | None,
    ) -> Iterator[str]:
        """Every file under the zip's `root` into `partial`; no member may leave it."""
        prefix = archive.root + "/"
        with zipfile.ZipFile(zip_path) as zipped:
            members = zipped.infolist()
            strangers = [m.filename for m in members if not m.filename.startswith(prefix)]
            if strangers:
                raise InstallerError(
                    f"{zip_path.name} has files outside its {archive.root} folder "
                    f"({strangers[0]}), so it was not unpacked."
                )
            files = [m for m in members if not m.is_dir()]
            yield f"Unpacking {len(files)} files into {dest.relative_to(server_dir)}"
            for count, member in enumerate(files, start=1):
                self._check_cancel(cancel, server_dir)
                rel = PurePosixPath(member.filename[len(prefix) :])
                if rel.is_absolute() or ".." in rel.parts or ":" in member.filename:
                    raise InstallerError(
                        f"{zip_path.name} names {member.filename!r}, which is not a path "
                        "inside its folder, so it was not unpacked."
                    )
                target = partial.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member, mode="r") as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
                if count % PROGRESS_EVERY == 0:
                    yield f"  {count} of {len(files)} files"

    def _unpack_tar(
        self,
        archive: VerifiedArchive,
        tar_path: Path,
        partial: Path,
        dest: Path,
        server_dir: Path,
        cancel: threading.Event | None,
    ) -> Iterator[str]:
        """`_unpack()` for a .tar.gz (Go's Linux release), keeping each program's execute bit.

        Regular files and folders only: anything else (a link, a device) is refused, never
        followed, because a link is how a tar member writes outside its folder. Go's own
        archive holds none (go1.26.5: 15,026 files, 1,667 folders, measured 2026-10-16).
        """
        prefix = archive.root + "/"
        with tarfile.open(tar_path, "r:gz") as tarred:
            members = tarred.getmembers()
            strangers = [
                m.name for m in members if m.name != archive.root and not m.name.startswith(prefix)
            ]
            if strangers:
                raise InstallerError(
                    f"{tar_path.name} has files outside its {archive.root} folder "
                    f"({strangers[0]}), so it was not unpacked."
                )
            odd = [m.name for m in members if not (m.isreg() or m.isdir())]
            if odd:
                raise InstallerError(
                    f"{tar_path.name} holds {odd[0]!r}, which is not a file or a folder, so it "
                    "was not unpacked."
                )
            files = [m for m in members if m.isreg()]
            yield f"Unpacking {len(files)} files into {dest.relative_to(server_dir)}"
            for count, member in enumerate(files, start=1):
                self._check_cancel(cancel, server_dir)
                rel = PurePosixPath(member.name[len(prefix) :])
                if rel.is_absolute() or ".." in rel.parts or ":" in member.name:
                    raise InstallerError(
                        f"{tar_path.name} names {member.name!r}, which is not a path inside "
                        "its folder, so it was not unpacked."
                    )
                source = tarred.extractfile(member)
                if source is None:
                    raise InstallerError(f"{tar_path.name}: {member.name} could not be read.")
                target = partial.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as out:
                    shutil.copyfileobj(source, out, CHUNK)
                os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
                if count % PROGRESS_EVERY == 0:
                    yield f"  {count} of {len(files)} files"
